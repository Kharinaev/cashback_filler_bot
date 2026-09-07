from datetime import datetime
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
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
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(
        self,
        credentials_file,
        spreadsheet_id,
        cashbacks_sheet="Cashbacks",
        categories_sheet="Categories",
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
        self.required_fields = [
            "Category",
            "Percent",
            "Bank",
            "Person",
            "Date",
        ]
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
        if not cashbacks_header or cashbacks_header[0] != self.COLUMNS:
            raise ValueError("Cashbacks sheet has an unexpected header")
        if not categories_header or categories_header[0] != ["Category"]:
            raise ValueError("Categories sheet has an unexpected header")

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
        self.client.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": requests},
        ).execute()

    def get_all_rows(self):
        values = self._get_values(self.cashbacks_sheet, "A2:G")
        rows = []
        for values_row in values:
            padded = values_row + [""] * (len(self.COLUMNS) - len(values_row))
            rows.append(dict(zip(self.COLUMNS, padded)))
        return rows

    def get_current_month_rows(self):
        month = datetime.now().strftime("%Y-%m")
        return [
            row
            for row in self.get_all_rows()
            if str(row["Date"]).startswith(month)
        ]

    def check_row_data(self, row_data):
        for field in self.required_fields:
            if field not in row_data:
                raise ValueError(f'No "{field}" specified: {row_data}')

    def _serialize_row(self, row_data):
        self.check_row_data(row_data)
        return [row_data.get(column, "") for column in self.COLUMNS]

    def add_row_to_database(self, row_data):
        return (
            self.client.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.spreadsheet_id,
                range=self._range(self.cashbacks_sheet, "A:G"),
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [self._serialize_row(row_data)]},
            )
            .execute()
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
