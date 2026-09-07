import argparse

import yaml
from src.google_sheets_api import GoogleSheetsDB
from src.sheet_colors import conditional_color_rule, value_color


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


def validation_request(
    sheet_id,
    column_index,
    source_range,
    message,
    row_count=TARGET_ROWS,
):
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": row_count,
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


def card_validation_request(sheet_id, argument_separator=","):
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": REFERENCE_ROWS,
                "startColumnIndex": 2,
                "endColumnIndex": 3,
            },
            "rule": {
                "condition": {
                    "type": "CUSTOM_FORMULA",
                    "values": [
                        {
                            "userEnteredValue": (
                                "=REGEXMATCH(TO_TEXT(C2)"
                                f'{argument_separator}"^[0-9]{{4}}$")'
                            )
                        }
                    ],
                },
                "inputMessage": "Введите последние четыре цифры карты",
                "strict": True,
                "showCustomUi": True,
            },
            "filteredRowsIncluded": True,
        }
    }


def plain_text_request(sheet_id, column_index, row_count):
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": row_count,
                "startColumnIndex": column_index,
                "endColumnIndex": column_index + 1,
            },
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {"type": "TEXT", "pattern": "@"}
                }
            },
            "fields": "userEnteredFormat.numberFormat",
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
    properties = (
        database.client.spreadsheets()
        .get(
            spreadsheetId=database.spreadsheet_id,
            fields="properties(locale)",
        )
        .execute()["properties"]
    )
    formula_separator = (
        ";" if properties.get("locale", "").startswith("ru") else ","
    )
    values = database._get_values(database.cashbacks_sheet, "A2:G")
    references = database._get_values(database.categories_sheet, "A2:C")
    cards_values = database._get_values(database.cards_sheet, "A2:C")

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
    cards = sheets[database.cards_sheet]
    cashbacks_id = cashbacks["properties"]["sheetId"]
    dictionaries_id = dictionaries["properties"]["sheetId"]
    cards_id = cards["properties"]["sheetId"]

    requests = []
    for sheet in (cashbacks, dictionaries, cards):
        for banding in sheet.get("bandedRanges", []):
            requests.append(
                {"deleteBanding": {"bandedRangeId": banding["bandedRangeId"]}}
            )
    for sheet, target_columns in (
        (cashbacks, {0, 2, 3}),
        (dictionaries, {0, 1, 2}),
        (cards, {0, 1}),
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
        (cards_id, REFERENCE_ROWS),
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
            header_format(cards_id, 3),
            banding_request(cashbacks_id, TARGET_ROWS, 7),
            banding_request(dictionaries_id, REFERENCE_ROWS, 3),
            banding_request(cards_id, REFERENCE_ROWS, 3),
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
            validation_request(
                cards_id,
                0,
                "=Categories!$B$2:$B$1000",
                "Выберите пользователя из справочника",
                row_count=REFERENCE_ROWS,
            ),
            validation_request(
                cards_id,
                1,
                "=Categories!$C$2:$C$1000",
                "Выберите банк из справочника",
                row_count=REFERENCE_ROWS,
            ),
            card_validation_request(cards_id, formula_separator),
            plain_text_request(cards_id, 2, REFERENCE_ROWS),
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
            {
                "setBasicFilter": {
                    "filter": {
                        "range": {
                            "sheetId": cards_id,
                            "startRowIndex": 0,
                            "endRowIndex": REFERENCE_ROWS,
                            "startColumnIndex": 0,
                            "endColumnIndex": 3,
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
    requests.extend(
        dimension_request(cards_id, index, width)
        for index, width in enumerate([160, 140, 120])
    )
    for category in categories:
        color = value_color("Category", category)
        requests.append(
            conditional_color_rule(
                cashbacks_id, 0, TARGET_ROWS, category, color
            )
        )
        requests.append(
            conditional_color_rule(
                dictionaries_id, 0, REFERENCE_ROWS, category, color
            )
        )
    for person in people:
        color = value_color("Person", person)
        requests.append(
            conditional_color_rule(cashbacks_id, 3, TARGET_ROWS, person, color)
        )
        requests.append(
            conditional_color_rule(
                dictionaries_id, 1, REFERENCE_ROWS, person, color
            )
        )
        requests.append(
            conditional_color_rule(cards_id, 0, REFERENCE_ROWS, person, color)
        )
    for bank in banks:
        color = value_color("Bank", bank)
        requests.append(
            conditional_color_rule(cashbacks_id, 2, TARGET_ROWS, bank, color)
        )
        requests.append(
            conditional_color_rule(
                dictionaries_id, 2, REFERENCE_ROWS, bank, color
            )
        )
        requests.append(
            conditional_color_rule(cards_id, 1, REFERENCE_ROWS, bank, color)
        )

    database.client.spreadsheets().batchUpdate(
        spreadsheetId=database.spreadsheet_id,
        body={"requests": requests},
    ).execute()
    print("Google Sheet UI configured")
    print(f"Categories: {len(categories)}")
    print(f"People: {len(people)}")
    print(f"Banks: {len(banks)}")
    print(f"Cards: {len(cards_values)}")


if __name__ == "__main__":
    main()
