import hashlib


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


def value_color(field, value):
    if field == "Bank" and value in BANK_COLORS:
        return BANK_COLORS[value]
    digest = hashlib.sha256(str(value).encode("utf-8")).digest()
    return PALETTE[digest[0] % len(PALETTE)]


def conditional_color_rule(sheet_id, column_index, row_count, value, color):
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
            "index": 0,
        }
    }
