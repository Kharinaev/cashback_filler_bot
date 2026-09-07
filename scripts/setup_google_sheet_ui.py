import argparse

import yaml
from src.google_sheets_api import GoogleSheetsDB


TARGET_ROWS = 10000
REFERENCE_ROWS = 1000
PALETTE = [
    (0.85, 0.92, 1.00),
    (0.89, 0.96, 0.86),
    (1.00, 0.92, 0.82),
    (0.96, 0.87, 0.94),
    (0.89, 0.88, 1.00),
    (1.00, 0.96, 0.76),
    (0.84, 0.96, 0.95),
    (0.96, 0.91, 0.84),
]
BANK_COLORS = {
    "Tinkoff": (1.00, 0.93, 0.35),
    "Alfa": (1.00, 0.80, 0.80),
    "Ozon": (0.78, 0.88, 1.00),
}


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


def color_rule(sheet_id, column_index, row_count, value, color, index=0):
    red, green, blue = color
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [
                    {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": row_count,
                        "startColumnIndex": column_index,
                        "endColumnIndex": column_index + 1,
                    }
                ],
                "booleanRule": {
                    "condition": {
                        "type": "TEXT_EQ",
                        "values": [{"userEnteredValue": str(value)}],
                    },
                    "format": {
                        "backgroundColor": {
                            "red": red,
                            "green": green,
                            "blue": blue,
                        },
                        "textFormat": {
                            "foregroundColor": {
                                "red": 0.10,
                                "green": 0.10,
                                "blue": 0.10,
                            },
                            "bold": True,
                        },
                    },
                },
            },
            "index": index,
        }
    }


def rule_targets_columns(rule, target_columns):
    for cell_range in rule.get("ranges", []):
        start = cell_range.get("startColumnIndex", 0)
        end = cell_range.get("endColumnIndex", start + 1)
        if any(start <= column < end for column in target_columns):
            return True
    return False


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
                "bandedRanges(bandedRangeId),conditionalFormats(ranges))"
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
    for sheet, target_columns in (
        (cashbacks, {0, 2, 3}),
        (dictionaries, {0, 1, 2}),
    ):
        rules = sheet.get("conditionalFormats", [])
        for index in range(len(rules) - 1, -1, -1):
            if rule_targets_columns(rules[index], target_columns):
                requests.append(
                    {
                        "deleteConditionalFormatRule": {
                            "sheetId": sheet["properties"]["sheetId"],
                            "index": index,
                        }
                    }
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
    for index, category in enumerate(categories):
        color = PALETTE[index % len(PALETTE)]
        requests.append(
            color_rule(cashbacks_id, 0, TARGET_ROWS, category, color)
        )
        requests.append(
            color_rule(dictionaries_id, 0, REFERENCE_ROWS, category, color)
        )
    for index, person in enumerate(people):
        color = PALETTE[(index + 2) % len(PALETTE)]
        requests.append(color_rule(cashbacks_id, 3, TARGET_ROWS, person, color))
        requests.append(
            color_rule(dictionaries_id, 1, REFERENCE_ROWS, person, color)
        )
    for index, bank in enumerate(banks):
        color = BANK_COLORS.get(bank, PALETTE[index % len(PALETTE)])
        requests.append(color_rule(cashbacks_id, 2, TARGET_ROWS, bank, color))
        requests.append(
            color_rule(dictionaries_id, 2, REFERENCE_ROWS, bank, color)
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
