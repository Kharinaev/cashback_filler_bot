from datetime import datetime
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from src.bot_helpers import parse_card_last4
from src.sheet_colors import conditional_color_rule, value_color


class GoogleSheetsDB:
    COLUMNS = [
        "Category",
        "Percent",
        "Bank",
        "Person",
        "Date",
        "Limit, ₽",
        "Info",
    ]
    CARD_COLUMNS = ["Person", "Bank", "Card"]
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(
        self,
        credentials_file,
        spreadsheet_id,
        cashbacks_sheet="Cashbacks",
        categories_sheet="Categories",
        cards_sheet="Cards",
    ):
        credentials_path = Path(credentials_file)
        credentials = Credentials.from_service_account_file(
            credentials_path,
            scopes=self.SCOPES,
        )
        self.client = build(
            "sheets",
            "v4",
            credentials=credentials,
            cache_discovery=False,
        )
        self.spreadsheet_id = spreadsheet_id
        self.cashbacks_sheet = cashbacks_sheet
        self.categories_sheet = categories_sheet
        self.cards_sheet = cards_sheet
        self.required_fields = [
            "Category",
            "Percent",
            "Bank",
            "Person",
            "Date",
        ]
        self._ensure_cards_sheet()
        self._validate_schema()

    @staticmethod
    def _range(sheet, cells):
        escaped = sheet.replace("'", "''")
        return f"'{escaped}'!{cells}"

    def _get_values(self, sheet, cells):
        response = (
            self.client.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=self._range(sheet, cells),
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING",
            )
            .execute()
        )
        return response.get("values", [])

    def _validate_schema(self):
        cashbacks_header = self._get_values(
            self.cashbacks_sheet,
            "A1:G1",
        )
        categories_header = self._get_values(
            self.categories_sheet,
            "A1:A1",
        )
        cards_header = self._get_values(self.cards_sheet, "A1:C1")
        if not cashbacks_header or cashbacks_header[0] != self.COLUMNS:
            raise ValueError("Cashbacks sheet has an unexpected header")
        if not categories_header or categories_header[0] != ["Category"]:
            raise ValueError("Categories sheet has an unexpected header")
        if not cards_header or cards_header[0] != self.CARD_COLUMNS:
            raise ValueError("Cards sheet has an unexpected header")

    def _ensure_cards_sheet(self):
        metadata = (
            self.client.spreadsheets()
            .get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties(title)",
            )
            .execute()
        )
        titles = {sheet["properties"]["title"] for sheet in metadata["sheets"]}
        if self.cards_sheet not in titles:
            self.client.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
                    "requests": [
                        {
                            "addSheet": {
                                "properties": {"title": self.cards_sheet}
                            }
                        }
                    ]
                },
            ).execute()
        header = self._get_values(self.cards_sheet, "A1:C1")
        if not header:
            self.client.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=self._range(self.cards_sheet, "A1:C1"),
                valueInputOption="RAW",
                body={"values": [self.CARD_COLUMNS]},
            ).execute()

    def get_unique_categories(self):
        return self.get_reference_values("Category")

    def get_reference_values(self, field):
        columns = {"Category": "A", "Person": "B", "Bank": "C"}
        if field not in columns:
            raise ValueError(f"Unknown reference field: {field}")
        column = columns[field]
        values = self._get_values(
            self.categories_sheet,
            f"{column}2:{column}",
        )
        result = []
        seen = set()
        for row in values:
            value = str(row[0]).strip() if row else ""
            if value and value not in seen:
                result.append(value)
                seen.add(value)
        return result

    def ensure_reference_value(self, field, value):
        value = str(value).strip()
        existing = self.get_reference_values(field)
        if not value or value in existing:
            return
        columns = {"Category": "A", "Person": "B", "Bank": "C"}
        column = columns[field]
        next_row = len(existing) + 2
        (
            self.client.spreadsheets()
            .values()
            .update(
                spreadsheetId=self.spreadsheet_id,
                range=self._range(
                    self.categories_sheet,
                    f"{column}{next_row}",
                ),
                valueInputOption="RAW",
                body={"values": [[value]]},
            )
            .execute()
        )
        self._add_reference_color(field, value)

    def _add_reference_color(self, field, value):
        target_columns = {"Category": 0, "Person": 3, "Bank": 2}
        reference_columns = {"Category": 0, "Person": 1, "Bank": 2}
        metadata = (
            self.client.spreadsheets()
            .get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties(sheetId,title)",
            )
            .execute()
        )
        sheet_ids = {
            sheet["properties"]["title"]: sheet["properties"]["sheetId"]
            for sheet in metadata["sheets"]
        }
        color = value_color(field, value)
        requests = [
            conditional_color_rule(
                sheet_ids[self.cashbacks_sheet],
                target_columns[field],
                10000,
                value,
                color,
            ),
            conditional_color_rule(
                sheet_ids[self.categories_sheet],
                reference_columns[field],
                1000,
                value,
                color,
            ),
        ]
        if field in ("Person", "Bank") and self.cards_sheet in sheet_ids:
            card_columns = {"Person": 0, "Bank": 1}
            requests.append(
                conditional_color_rule(
                    sheet_ids[self.cards_sheet],
                    card_columns[field],
                    1000,
                    value,
                    color,
                )
            )
        self.client.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": requests},
        ).execute()

    def get_all_rows(self):
        return [row for _, row in self.get_all_rows_with_indices()]

    def get_all_rows_with_indices(self):
        values = self._get_values(self.cashbacks_sheet, "A2:G")
        rows = []
        for row_number, values_row in enumerate(values, start=2):
            padded = values_row + [""] * (len(self.COLUMNS) - len(values_row))
            rows.append((row_number, dict(zip(self.COLUMNS, padded))))
        return rows

    def get_month_rows(self, month):
        month_prefix = str(month)[:7]
        return [
            row
            for row in self.get_all_rows()
            if str(row["Date"]).startswith(month_prefix)
        ]

    def get_month_rows_with_indices(self, month):
        month_prefix = str(month)[:7]
        return [
            (row_number, row)
            for row_number, row in self.get_all_rows_with_indices()
            if str(row["Date"]).startswith(month_prefix)
        ]

    def get_current_month_rows(self):
        return self.get_month_rows(datetime.now().strftime("%Y-%m"))

    @classmethod
    def row_key(cls, row_data):
        values = []
        for column in cls.COLUMNS:
            value = row_data.get(column, "")
            if column == "Percent":
                try:
                    value = f"{float(value):g}"
                except (TypeError, ValueError):
                    pass
            values.append(cls._comparable_value(value))
        return tuple(values)

    @staticmethod
    def _comparable_value(value):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    def _delete_sheet_row(self, sheet_name, columns, row_number, expected_row):
        current = self._get_values(
            sheet_name,
            f"A{row_number}:{chr(64 + len(columns))}{row_number}",
        )
        if not current:
            raise ValueError("The selected row no longer exists")
        actual = current[0] + [""] * (len(columns) - len(current[0]))
        expected = [expected_row.get(column, "") for column in columns]
        if [self._comparable_value(value) for value in actual] != [
            self._comparable_value(value) for value in expected
        ]:
            raise ValueError("The selected row changed")

        metadata = (
            self.client.spreadsheets()
            .get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties(sheetId,title)",
            )
            .execute()
        )
        sheet_id = next(
            sheet_item["properties"]["sheetId"]
            for sheet_item in metadata["sheets"]
            if sheet_item["properties"]["title"] == sheet_name
        )
        (
            self.client.spreadsheets()
            .batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
                    "requests": [
                        {
                            "deleteDimension": {
                                "range": {
                                    "sheetId": sheet_id,
                                    "dimension": "ROWS",
                                    "startIndex": row_number - 1,
                                    "endIndex": row_number,
                                }
                            }
                        }
                    ]
                },
            )
            .execute()
        )
        return None

    def delete_row(self, row_number, expected_row):
        self.check_row_data(expected_row)
        return self._delete_sheet_row(
            self.cashbacks_sheet,
            self.COLUMNS,
            row_number,
            expected_row,
        )

    def check_row_data(self, row_data):
        for field in self.required_fields:
            if field not in row_data:
                raise ValueError(f'No "{field}" specified: {row_data}')

    def _serialize_row(self, row_data):
        self.check_row_data(row_data)
        return [row_data.get(column, "") for column in self.COLUMNS]

    def add_row_to_database(self, row_data):
        saved, _ = self.add_rows_if_new([row_data])
        return bool(saved)

    def add_rows_if_new(self, rows):
        existing_keys = {self.row_key(row) for row in self.get_all_rows()}
        saved = []
        duplicates = []
        for row in rows:
            self.check_row_data(row)
            key = self.row_key(row)
            if key in existing_keys:
                duplicates.append(row)
                continue
            saved.append(row)
            existing_keys.add(key)
        if not saved:
            return saved, duplicates
        (
            self.client.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.spreadsheet_id,
                range=self._range(self.cashbacks_sheet, "A:G"),
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [self._serialize_row(row) for row in saved]},
            )
            .execute()
        )
        return saved, duplicates

    def get_cards_with_indices(self):
        values = self._get_values(self.cards_sheet, "A2:C")
        rows = []
        for row_number, values_row in enumerate(values, start=2):
            padded = values_row + [""] * (
                len(self.CARD_COLUMNS) - len(values_row)
            )
            if not any(str(value).strip() for value in padded):
                continue
            row = dict(zip(self.CARD_COLUMNS, padded))
            row["Card"] = str(row["Card"]).strip().zfill(4)
            rows.append((row_number, row))
        return rows

    def get_cards(self, person=None, bank=None):
        rows = [row for _, row in self.get_cards_with_indices()]
        if person is not None:
            rows = [row for row in rows if row["Person"] == person]
        if bank is not None:
            rows = [row for row in rows if row["Bank"] == bank]
        return rows

    def add_card(self, person, bank, card):
        card = parse_card_last4(card)
        existing = self.get_cards(person=person, bank=bank)
        if any(row["Card"] == card for row in existing):
            return False
        self.client.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=self._range(self.cards_sheet, "A:C"),
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[person, bank, card]]},
        ).execute()
        return True

    def delete_card(self, row_number, expected_row):
        return self._delete_sheet_row(
            self.cards_sheet,
            self.CARD_COLUMNS,
            row_number,
            expected_row,
        )

    def replace_rows(self, rows):
        values_api = self.client.spreadsheets().values()
        values_api.clear(
            spreadsheetId=self.spreadsheet_id,
            range=self._range(self.cashbacks_sheet, "A2:G"),
            body={},
        ).execute()
        serialized = [self._serialize_row(row) for row in rows]
        if serialized:
            values_api.update(
                spreadsheetId=self.spreadsheet_id,
                range=self._range(self.cashbacks_sheet, "A2"),
                valueInputOption="RAW",
                body={"values": serialized},
            ).execute()

    def replace_categories(self, categories):
        values_api = self.client.spreadsheets().values()
        values_api.clear(
            spreadsheetId=self.spreadsheet_id,
            range=self._range(self.categories_sheet, "A2:A"),
            body={},
        ).execute()
        values = [[category] for category in categories if category]
        if values:
            values_api.update(
                spreadsheetId=self.spreadsheet_id,
                range=self._range(self.categories_sheet, "A2"),
                valueInputOption="RAW",
                body={"values": values},
            ).execute()
