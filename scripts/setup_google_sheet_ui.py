import argparse

import yaml
from src.google_sheets_api import GoogleSheetsDB


TARGET_ROWS = 10000
REFERENCE_ROWS = 1000


def unique_strings(values):
    result = []
    seen = set()
    for value in values:
        normalized = str(value).strip() if value is not None else ""
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def column(values, index):
    return [row[index] for row in values if len(row) > index]


def validation_request(sheet_id, column_index, source_range, message):
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": TARGET_ROWS,
                "startColumnIndex": column_index,
                "endColumnIndex": column_index + 1,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_RANGE",
                    "values": [{"userEnteredValue": source_range}],
                },
                "inputMessage": message,
                "strict": True,
                "showCustomUi": True,
            },
            "filteredRowsIncluded": True,
        }
    }


def header_format(sheet_id, column_count):
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": column_count,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {
                        "red": 0.12,
                        "green": 0.31,
                        "blue": 0.47,
                    },
                    "textFormat": {
                        "bold": True,
                        "foregroundColor": {
                            "red": 1.0,
                            "green": 1.0,
                            "blue": 1.0,
                        },
                    },
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                }
            },
            "fields": "userEnteredFormat",
        }
    }


def dimension_request(sheet_id, index, width):
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": index,
                "endIndex": index + 1,
            },
            "properties": {"pixelSize": width},
            "fields": "pixelSize",
        }
    }


def banding_request(sheet_id, row_count, column_count):
    return {
        "addBanding": {
            "bandedRange": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": row_count,
                    "startColumnIndex": 0,
                    "endColumnIndex": column_count,
                },
                "rowProperties": {
                    "headerColor": {
                        "red": 0.12,
                        "green": 0.31,
                        "blue": 0.47,
                    },
                    "firstBandColor": {
                        "red": 0.95,
                        "green": 0.97,
                        "blue": 0.99,
                    },
                    "secondBandColor": {
                        "red": 1.0,
                        "green": 1.0,
                        "blue": 1.0,
                    },
                },
            }
        }
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/config.yaml")
    args = parser.parse_args()

    with open(args.config, "r") as config_file:
        config = yaml.safe_load(config_file)

    database = GoogleSheetsDB(**config["db"])
    values = database._get_values(database.cashbacks_sheet, "A2:G")
    references = database._get_values(database.categories_sheet, "A2:C")

    configured_people = [
        user["db_username"] for user in config["bot"].get("users", [])
    ]
    categories = unique_strings(column(references, 0))
    people = unique_strings(
        column(references, 1) + column(values, 3) + configured_people
    )
    banks = unique_strings(
        column(references, 2) + column(values, 2) + ["Tinkoff", "Alfa", "Ozon"]
    )

    values_api = database.client.spreadsheets().values()
    values_api.update(
        spreadsheetId=database.spreadsheet_id,
        range=database._range(database.categories_sheet, "A1:C1"),
        valueInputOption="RAW",
        body={"values": [["Category", "Person", "Bank"]]},
    ).execute()
    values_api.clear(
        spreadsheetId=database.spreadsheet_id,
        range=database._range(database.categories_sheet, "A2:C"),
        body={},
    ).execute()
    values_api.update(
        spreadsheetId=database.spreadsheet_id,
        range=database._range(database.categories_sheet, "A2"),
        valueInputOption="RAW",
        body={"values": [[value] for value in categories]},
    ).execute()
    values_api.update(
        spreadsheetId=database.spreadsheet_id,
        range=database._range(database.categories_sheet, "B2"),
        valueInputOption="RAW",
        body={"values": [[value] for value in people]},
    ).execute()
    values_api.update(
        spreadsheetId=database.spreadsheet_id,
        range=database._range(database.categories_sheet, "C2"),
        valueInputOption="RAW",
        body={"values": [[value] for value in banks]},
    ).execute()

    metadata = (
        database.client.spreadsheets()
        .get(
            spreadsheetId=database.spreadsheet_id,
            fields=(
                "sheets(properties(sheetId,title,gridProperties(rowCount)),"
                "bandedRanges(bandedRangeId))"
            ),
        )
        .execute()
    )
    sheets = {
        sheet["properties"]["title"]: sheet for sheet in metadata["sheets"]
    }
    cashbacks = sheets[database.cashbacks_sheet]
    dictionaries = sheets[database.categories_sheet]
    cashbacks_id = cashbacks["properties"]["sheetId"]
    dictionaries_id = dictionaries["properties"]["sheetId"]

    requests = []
    for sheet in (cashbacks, dictionaries):
        for banding in sheet.get("bandedRanges", []):
            requests.append(
                {"deleteBanding": {"bandedRangeId": banding["bandedRangeId"]}}
            )
    for sheet_id, row_count in (
        (cashbacks_id, TARGET_ROWS),
        (dictionaries_id, REFERENCE_ROWS),
    ):
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {
                            "rowCount": row_count,
                            "frozenRowCount": 1,
                            "hideGridlines": True,
                        },
                    },
                    "fields": (
                        "gridProperties(rowCount,frozenRowCount,hideGridlines)"
                    ),
                }
            }
        )

    requests.extend(
        [
            header_format(cashbacks_id, 7),
            header_format(dictionaries_id, 3),
            banding_request(cashbacks_id, TARGET_ROWS, 7),
            banding_request(dictionaries_id, REFERENCE_ROWS, 3),
            validation_request(
                cashbacks_id,
                0,
                "=Categories!$A$2:$A$1000",
                "Выберите категорию из справочника",
            ),
            validation_request(
                cashbacks_id,
                2,
                "=Categories!$C$2:$C$1000",
                "Выберите банк из справочника",
            ),
            validation_request(
                cashbacks_id,
                3,
                "=Categories!$B$2:$B$1000",
                "Выберите пользователя из справочника",
            ),
            {
                "setBasicFilter": {
                    "filter": {
                        "range": {
                            "sheetId": cashbacks_id,
                            "startRowIndex": 0,
                            "endRowIndex": TARGET_ROWS,
                            "startColumnIndex": 0,
                            "endColumnIndex": 7,
                        }
                    }
                }
            },
        ]
    )

    widths = [230, 90, 110, 120, 105, 100, 260]
    requests.extend(
        dimension_request(cashbacks_id, index, width)
        for index, width in enumerate(widths)
    )
    requests.extend(
        dimension_request(dictionaries_id, index, width)
        for index, width in enumerate([230, 140, 120])
    )

    database.client.spreadsheets().batchUpdate(
        spreadsheetId=database.spreadsheet_id,
        body={"requests": requests},
    ).execute()
    print("Google Sheet UI configured")
    print(f"Categories: {len(categories)}")
    print(f"People: {len(people)}")
    print(f"Banks: {len(banks)}")


if __name__ == "__main__":
    main()
