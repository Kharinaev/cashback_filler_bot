from datetime import datetime

import pytest
from src.bot_helpers import (
    automatic_month_start,
    canonical_bank,
    category_match_score,
    frequent_values,
    month_start,
    parse_card_last4,
    parse_percent,
    search_category_rows,
)
from src.category_emojis import category_emoji, normalize_category_emoji
from src.sheet_colors import value_color


def test_canonical_bank_accepts_common_names():
    assert canonical_bank("Т-Банк") == "Tinkoff"
    assert canonical_bank("тиньк") == "Tinkoff"
    assert canonical_bank("Альфа Банк") == "Alfa"
    assert canonical_bank("озон") == "Ozon"
    assert canonical_bank("Новый банк") == "Новый банк"


@pytest.mark.parametrize("raw", ["1234", "0123"])
def test_parse_card_last4_preserves_four_digits(raw):
    assert parse_card_last4(raw) == raw


@pytest.mark.parametrize("raw", ["123", "12345", "12 34", "abcd"])
def test_parse_card_last4_rejects_invalid_values(raw):
    with pytest.raises(ValueError):
        parse_card_last4(raw)


def test_category_match_handles_case_typo_and_substring():
    assert category_match_score("такси", "Такси") == 1.0
    assert category_match_score("аптек", "Аптеки") >= 0.55
    assert category_match_score("такси кешбек", "Такси") == 0.9


def test_search_category_rows_returns_only_relevant_matches():
    rows = [
        {"Category": "Аптеки", "Person": "A", "Bank": "Alfa"},
        {"Category": "Такси", "Person": "B", "Bank": "Tinkoff"},
    ]
    assert search_category_rows(rows, "аптека") == [rows[0]]


def test_frequent_values_filters_and_adds_defaults():
    rows = [
        {"Person": "A", "Bank": "Alfa", "Category": "Такси"},
        {"Person": "A", "Bank": "Alfa", "Category": "Такси"},
        {"Person": "A", "Bank": "Alfa", "Category": "Аптеки"},
        {"Person": "B", "Bank": "Alfa", "Category": "АЗС"},
    ]
    result = frequent_values(
        rows,
        "Category",
        Person="A",
        Bank="Alfa",
        defaults=("Другое",),
    )
    assert result == ["Такси", "Аптеки", "Другое"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1,5%", 1.5), ("10", 10), (0, 0)],
)
def test_parse_percent(raw, expected):
    assert parse_percent(raw) == expected


def test_parse_percent_rejects_out_of_range():
    with pytest.raises(ValueError):
        parse_percent("101")


def test_month_start_handles_year_boundary():
    now = datetime(2026, 1, 15)
    assert month_start(0, now) == "2026-01-01"
    assert month_start(-1, now) == "2025-12-01"


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 9, 25, 23, 59), "2026-09-01"),
        (datetime(2026, 9, 26, 0, 0), "2026-10-01"),
        (datetime(2026, 12, 31), "2027-01-01"),
    ],
)
def test_automatic_month_rolls_over_after_25th(now, expected):
    assert automatic_month_start(now) == expected


def test_category_emojis_are_semantic_and_invalid_vlm_value_falls_back():
    assert category_emoji("Супермаркеты в Городе") == "🛒"
    assert category_emoji("Аптеки") == "💊"
    assert normalize_category_emoji("🚕", "Такси") == "🚕"
    assert normalize_category_emoji("taxi", "Такси") == "🚕"
    assert normalize_category_emoji(None, "Аптеки") == "💊"


def test_colors_are_stable_and_banks_have_distinct_colors():
    assert value_color("Category", "Такси") == value_color("Category", "Такси")
    assert value_color("Bank", "Tinkoff") != value_color("Bank", "Alfa")
