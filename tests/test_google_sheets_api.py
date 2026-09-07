from unittest.mock import MagicMock

import pytest


GoogleSheetsDB = pytest.importorskip("src.google_sheets_api").GoogleSheetsDB


def make_db(current_values):
    db = object.__new__(GoogleSheetsDB)
    db.spreadsheet_id = "spreadsheet"
    db.cashbacks_sheet = "Cashbacks"
    db.required_fields = ["Category", "Percent", "Bank", "Person", "Date"]
    db._get_values = MagicMock(return_value=[current_values])
    db.client = MagicMock()
    sheets = db.client.spreadsheets.return_value
    sheets.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": "Cashbacks", "sheetId": 123}}]
    }
    return db


def test_delete_row_checks_contents_and_deletes_exact_sheet_row():
    values = ["Такси", 5, "Tinkoff", "Иван", "2026-09-01", "", ""]
    db = make_db(values)
    row = dict(zip(db.COLUMNS, values))

    db.delete_row(7, row)

    request = db.client.spreadsheets.return_value.batchUpdate.call_args.kwargs
    delete_range = request["body"]["requests"][0]["deleteDimension"]["range"]
    assert delete_range == {
        "sheetId": 123,
        "dimension": "ROWS",
        "startIndex": 6,
        "endIndex": 7,
    }


def test_delete_row_refuses_to_delete_when_row_changed():
    values = ["Такси", 5, "Tinkoff", "Иван", "2026-09-01", "", ""]
    db = make_db(values)
    changed = dict(zip(db.COLUMNS, values))
    changed["Percent"] = 10

    with pytest.raises(ValueError, match="changed"):
        db.delete_row(7, changed)

    db.client.spreadsheets.return_value.batchUpdate.assert_not_called()
