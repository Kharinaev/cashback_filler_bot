import pytest


bot = pytest.importorskip("src.bot")


def test_cashback_list_includes_all_cards_for_person_and_bank():
    rows = [
        {
            "Person": "Алёна",
            "Bank": "Tinkoff",
            "Category": "Транспорт",
            "Percent": 5,
        }
    ]
    cards = [
        {"Person": "Алёна", "Bank": "Tinkoff", "Card": "0123"},
        {"Person": "Алёна", "Bank": "Tinkoff", "Card": "9876"},
        {"Person": "Алёна", "Bank": "Alfa", "Card": "5555"},
    ]

    text = bot.format_cashback_list(rows, cards, {"Транспорт": "🚌"})

    assert "👤 Алёна · 🟡 ТБанк" in text
    assert "Карты 0123, 9876" in text
    assert "1. 🚌 Транспорт — 5%" in text
    assert "5555" not in text


def test_duplicate_message_reports_saved_and_skipped_counts():
    assert bot.save_result_message([{}], [{}, {}]) == (
        "✅ Сохранено записей: 1.\nℹ️ Полных дублей пропущено: 2."
    )


def test_screenshot_preview_groups_bank_and_shows_category_emojis():
    rows = [
        {
            "Bank": "Tinkoff",
            "Category": "Такси",
            "Emoji": "🚕",
            "Percent": 5,
        },
        {
            "Bank": "Tinkoff",
            "Category": "Аптеки",
            "Emoji": "💊",
            "Percent": 3,
        },
    ]

    text = bot.format_rows_preview(rows)

    assert text.count("🟡 ТБанк") == 1
    assert "1. 🚕 Такси — 5%" in text
    assert "2. 💊 Аптеки — 3%" in text


def test_help_lists_commands_and_automatic_month_rule():
    for command in (
        "/add",
        "/list",
        "/delete",
        "/cards",
        "/month",
        "/cancel",
        "/start",
    ):
        assert command in bot.HELP_MESSAGE
    assert "с 26-го — следующий" in bot.HELP_MESSAGE
    assert "Полные дубли не добавляются" in bot.HELP_MESSAGE
