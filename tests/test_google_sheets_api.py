from unittest.mock import MagicMock

import pytest


GoogleSheetsDB = pytest.importorskip("src.google_sheets_api").GoogleSheetsDB


def make_db(current_values):
    db = object.__new__(GoogleSheetsDB)
    db.spreadsheet_id = "spreadsheet"
    db.cashbacks_sheet = "Cashbacks"
    db.cards_sheet = "Cards"
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


def test_add_rows_if_new_skips_existing_and_incoming_duplicates():
    existing = ["Такси", 5, "Tinkoff", "Иван", "2026-09-01", "", ""]
    db = make_db(existing)
    duplicate = dict(zip(db.COLUMNS, existing))
    duplicate["Percent"] = 5.0
    new = {**duplicate, "Category": "Аптеки"}

    saved, duplicates = db.add_rows_if_new([duplicate, new, new.copy()])

    assert saved == [new]
    assert duplicates == [duplicate, new]
    appended = db.client.spreadsheets.return_value.values.return_value.append
    assert appended.call_args.kwargs["body"]["values"] == [
        db._serialize_row(new)
    ]


def test_add_card_skips_duplicate():
    db = make_db([])
    db.get_cards = MagicMock(
        return_value=[{"Person": "Иван", "Bank": "Alfa", "Card": "0123"}]
    )

    assert db.add_card("Иван", "Alfa", "0123") is False
    db.client.spreadsheets.return_value.values.return_value.append.assert_not_called()
