import html
import logging
import os
from functools import partial

from src.bot_helpers import (
    automatic_month_start,
    canonical_bank,
    frequent_values,
    month_start,
    parse_card_last4,
    parse_percent,
    search_category_rows,
)
from src.category_emojis import category_emoji
from src.pipe import Pipeline
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    filters,
)


logger = logging.getLogger("logger")


# State management for editing
class EditState:
    def __init__(self, rows, image_path, db_username):
        self.rows = rows
        self.image_path = image_path
        self.db_username = db_username
        self.edited_rows = rows.copy()
        self.current_edit = None  # ('bank', None) or ('category', row_idx) or ('percent', row_idx)
        self.bank_options = []


class ManualAddState:
    def __init__(self, person, date):
        self.person = person
        self.date = date
        self.stage = "bank"
        self.bank = None
        self.category = None
        self.emoji = None
        self.percent = None
        self.options = []


class DeleteState:
    def __init__(self, date, rows):
        self.date = date
        self.rows = rows
        self.stage = "person"
        self.person = None
        self.bank = None
        self.record = None
        self.bulk = False
        self.options = []
        self.category_emojis = {}


class CardState:
    def __init__(self, action, rows=None):
        self.action = action
        self.rows = rows or []
        self.stage = "person"
        self.person = None
        self.bank = None
        self.card = None
        self.record = None
        self.options = []


# Store edit states for each user
edit_states = {}
manual_states = {}
delete_states = {}
card_states = {}


# Display name and color emoji for each bank.
BANK_DISPLAY = {
    "Tinkoff": ("ТБанк", "🟡"),
    "Alfa": ("Альфа", "🔴"),
    "Ozon": ("Ozon", "🔵"),
}

# Order in which banks are shown within a person's cashbacks.
BANK_ORDER = ["Tinkoff", "Alfa", "Ozon"]


HELP_MESSAGE = """<b>Что умеет бот</b>

📸 <b>Распознавать скриншоты</b>
Отправьте скриншот с кэшбэками. Бот определит банк, категории и проценты, предложит всё проверить и позволит исправить результат перед сохранением. Полные дубли не добавляются.

🔎 <b>Искать категории</b>
Напишите название категории обычным сообщением. Бот найдёт похожие категории выбранного месяца и покажет пользователя, банк, процент и карты для оплаты.

➕ <b>/add — добавить категорию вручную</b>
Последовательно выберите банк, категорию и процент или введите свои значения.

📋 <b>/list — показать кэшбэки</b>
Выводит категории выбранного месяца по пользователям и банкам вместе с картами.

🗑 <b>/delete — удалить категории</b>
Можно удалить одну запись или сразу все категории конкретного пользователя и банка за выбранный месяц.

💳 <b>/cards — управлять картами</b>
Добавление и удаление последних четырёх цифр карт.

📅 <b>/month — выбрать рабочий месяц</b>
В авторежиме до 25-го включительно используется текущий месяц, с 26-го — следующий. При необходимости можно зафиксировать месяц вручную.

❌ <b>/cancel — отменить действие</b>
🏠 <b>/start — открыть главное меню</b>"""


def bank_label(bank):
    name, emoji = BANK_DISPLAY.get(bank, (bank or "—", "⚪️"))
    return f"{emoji} {name}"


def format_percent(percent):
    if percent in (None, ""):
        return "—"
    number = float(percent)
    if number.is_integer():
        return f"{int(number)}%"
    return f"{number:g}%"


def emoji_for_category(category, category_emojis=None, row=None):
    if row and row.get("Emoji"):
        return row["Emoji"]
    if category_emojis and category in category_emojis:
        return category_emojis[category]
    return category_emoji(category)


def category_label(category, category_emojis=None, row=None):
    category = str(category or "—")
    return f"{emoji_for_category(category, category_emojis, row)} {category}"


def format_rows_preview(
    rows,
    title="Проверьте распознанные категории",
    category_emojis=None,
):
    lines = [f"<b>{html.escape(title)}</b>"]
    grouped = {}
    for index, row in enumerate(rows, 1):
        grouped.setdefault(row.get("Bank"), []).append((index, row))
    for bank, bank_rows in grouped.items():
        lines.extend(["", f"<b>{html.escape(bank_label(bank))}</b>"])
        for index, row in bank_rows:
            label = category_label(row.get("Category"), category_emojis, row)
            lines.append(
                f"{index}. {html.escape(label)}"
                f" — {html.escape(format_percent(row.get('Percent')))}"
            )
    return "\n".join(lines)


def edit_bank_keyboard(options):
    buttons = [
        InlineKeyboardButton(
            bank_label(value), callback_data=f"edit_bank_option:{index}"
        )
        for index, value in enumerate(options)
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append(
        [InlineKeyboardButton("⬅️ Назад", callback_data="edit_bank_back")]
    )
    return InlineKeyboardMarkup(rows)


def apply_bank_to_edit(state, value):
    bank = canonical_bank(value)
    for row in state.edited_rows:
        row["Bank"] = bank
    state.current_edit = None


def edit_category_emojis(pipeline):
    return pipeline.db.get_category_emojis()


async def handle_edit_bank_action(query, state, pipeline):
    if query.data == "edit_bank":
        state.current_edit = ("bank", None)
        state.bank_options = pipeline.db.get_reference_values("Bank")
        await query.message.reply_text(
            "Выберите правильный банк или напишите свой вариант. "
            "Он применится ко всем строкам.",
            reply_markup=edit_bank_keyboard(state.bank_options),
        )
        return
    if query.data == "edit_bank_back":
        state.current_edit = None
        title = "Результат без изменений"
    else:
        try:
            index = int(query.data.split(":", 1)[1])
            value = state.bank_options[index]
        except (ValueError, IndexError):
            await query.message.reply_text("Этот вариант уже неактуален.")
            return
        apply_bank_to_edit(state, value)
        title = "Обновлённый результат"
    await query.message.reply_text(
        format_rows_preview(
            state.edited_rows,
            title,
            edit_category_emojis(pipeline),
        ),
        parse_mode="HTML",
        reply_markup=create_edit_keyboard(state.edited_rows),
    )


def card_numbers(cards, person, bank):
    return sorted(
        {
            str(row["Card"]).zfill(4)
            for row in cards
            if row["Person"] == person and row["Bank"] == bank
        }
    )


def format_cards_line(numbers):
    if not numbers:
        return "Карты не добавлены"
    if len(numbers) == 1:
        return f"Карта {numbers[0]}"
    return f"Карты {', '.join(numbers)}"


def save_result_message(saved, duplicates):
    if duplicates and not saved:
        return "ℹ️ Все записи уже есть в таблице — дубли не добавлены."
    if duplicates:
        return (
            f"✅ Сохранено записей: {len(saved)}.\n"
            f"ℹ️ Полных дублей пропущено: {len(duplicates)}."
        )
    if len(saved) == 1:
        return "✅ Категория сохранена."
    return f"✅ Сохранено записей: {len(saved)}."


def create_edit_keyboard(rows):
    keyboard = []

    keyboard.append(
        [InlineKeyboardButton("🏦 Изменить банк", callback_data="edit_bank")]
    )

    # Add row-specific edit buttons
    for i, row in enumerate(rows, 1):
        keyboard.extend(
            [
                [
                    InlineKeyboardButton(
                        f"✏️ Категория {i}", callback_data=f"edit_category_{i}"
                    ),
                    InlineKeyboardButton(
                        f"% Процент {i}", callback_data=f"edit_percent_{i}"
                    ),
                ]
            ]
        )

    # Add confirm and cancel buttons
    keyboard.append(
        [
            InlineKeyboardButton("✅ Сохранить", callback_data="confirm_edit"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit"),
        ]
    )

    return InlineKeyboardMarkup(keyboard)


async def handle_edit_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pipeline
) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    username = query.from_user.username
    state = edit_states.get(user_id)

    if not state:
        logger.warning(
            f"User @{username} (ID: {user_id}) tried to edit without active session"
        )
        await query.message.reply_text("Сессия проверки уже завершена.")
        return

    if query.data.startswith("edit_bank"):
        logger.info(f"User @{username} (ID: {user_id}) started bank edit")
        await handle_edit_bank_action(query, state, pipeline)

    elif query.data.startswith("edit_category_"):
        row_idx = int(query.data.split("_")[2]) - 1
        logger.info(
            f"User @{username} (ID: {user_id}) started category edit for row {row_idx + 1}"
        )
        state.current_edit = ("category", row_idx)
        await query.message.reply_text(
            f"Напишите правильную категорию для строки {row_idx + 1}."
        )

    elif query.data.startswith("edit_percent_"):
        row_idx = int(query.data.split("_")[2]) - 1
        logger.info(
            f"User @{username} (ID: {user_id}) started percent edit for row {row_idx + 1}"
        )
        state.current_edit = ("percent", row_idx)
        await query.message.reply_text(
            f"Напишите правильный процент для строки {row_idx + 1}."
        )

    elif query.data == "confirm_edit":
        logger.info(f"User @{username} (ID: {user_id}) confirmed edits")
        # Save to Google Sheets
        try:
            saved, duplicates = pipeline.save_rows_to_database(
                state.edited_rows
            )
            logger.info(
                f"Successfully saved edited rows for user @{username} (ID: {user_id})"
            )
            await query.message.reply_text(
                save_result_message(saved, duplicates),
                reply_markup=main_menu_keyboard(),
            )
        except Exception:
            logger.error(
                "Error saving changes for user @%s (ID: %s)",
                username,
                user_id,
            )
            await query.message.reply_text("❌ Не удалось сохранить изменения.")
        del edit_states[user_id]

    elif query.data == "cancel_edit":
        logger.info(f"User @{username} (ID: {user_id}) cancelled edits")
        await query.message.reply_text(
            "Проверка отменена.", reply_markup=main_menu_keyboard()
        )
        del edit_states[user_id]


async def handle_edit_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pipeline
) -> None:
    user_id = update.effective_user.id
    username = update.effective_user.username
    state = edit_states.get(user_id)

    if not state or not state.current_edit:
        return

    edit_type, row_idx = state.current_edit
    new_value = update.message.text

    if edit_type == "bank":
        # Update bank for all rows
        old_value = state.edited_rows[0]["Bank"]
        apply_bank_to_edit(state, new_value)
        logger.info(
            f"User @{username} (ID: {user_id}) changed bank from '{old_value}' to '{new_value}'"
        )
    elif edit_type == "category":
        old_value = state.edited_rows[row_idx]["Category"]
        state.edited_rows[row_idx]["Category"] = new_value
        state.edited_rows[row_idx]["Emoji"] = category_emoji(new_value)
        logger.info(
            f"User @{username} (ID: {user_id}) changed category in row {row_idx + 1} from '{old_value}' to '{new_value}'"
        )
    elif edit_type == "percent":
        try:
            old_value = state.edited_rows[row_idx]["Percent"]
            new_percent = parse_percent(new_value)
            state.edited_rows[row_idx]["Percent"] = new_percent
            logger.info(
                f"User @{username} (ID: {user_id}) changed percent in row {row_idx + 1} from {old_value} to {new_percent}"
            )
        except (TypeError, ValueError):
            logger.warning(
                f"User @{username} (ID: {user_id}) sent invalid percentage value: {new_value}"
            )
            await update.message.reply_text(
                "Процент должен быть числом от 0 до 100, например 1,5."
            )
            return

    await update.message.reply_text(
        format_rows_preview(
            state.edited_rows,
            "Обновлённый результат",
            edit_category_emojis(pipeline),
        ),
        parse_mode="HTML",
        reply_markup=create_edit_keyboard(state.edited_rows),
    )

    # Reset current edit
    state.current_edit = None


def main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Добавить", callback_data="menu:add"),
                InlineKeyboardButton("📋 Список", callback_data="menu:list"),
            ],
            [
                InlineKeyboardButton("🗑 Удалить", callback_data="menu:delete"),
                InlineKeyboardButton(
                    "📅 Выбрать месяц", callback_data="menu:month"
                ),
            ],
            [InlineKeyboardButton("💳 Карты", callback_data="menu:cards")],
        ]
    )


def selected_month(context):
    return context.user_data.get("target_month") or automatic_month_start()


def selected_month_label(context):
    value = context.user_data.get("target_month")
    return value[:7] if value else f"{automatic_month_start()[:7]} (авто)"


def option_keyboard(
    prefix,
    options,
    columns=2,
    cancel=True,
    back=False,
    labels=None,
):
    buttons = [
        InlineKeyboardButton(
            str(labels[index] if labels else option),
            callback_data=f"{prefix}:{index}",
        )
        for index, option in enumerate(options)
    ]
    rows = [
        buttons[index : index + columns]
        for index in range(0, len(buttons), columns)
    ]
    controls = []
    if back:
        controls.append(
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=f"{prefix.split(':')[0]}:back",
            )
        )
    if cancel:
        controls.append(
            InlineKeyboardButton(
                "❌ Отмена",
                callback_data=f"{prefix.split(':')[0]}:cancel",
            )
        )
    if controls:
        rows.append(controls)
    return InlineKeyboardMarkup(rows)


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    start_message: str = "",
    refuse_message: str = "",
    allowed_users: dict = {},
) -> None:
    username = update.effective_user.username
    if username in allowed_users:
        user_id = update.effective_user.id
        manual_states.pop(user_id, None)
        delete_states.pop(user_id, None)
        card_states.pop(user_id, None)
        edit_states.pop(user_id, None)
        await update.effective_message.reply_text(
            f"{start_message}\n\n"
            f"Рабочий месяц: {selected_month_label(context)}.\n"
            "Для поиска просто напишите название категории.",
            reply_markup=main_menu_keyboard(),
        )
        logger.info(f"Start command for user @{username}")
    else:
        await update.effective_message.reply_text(refuse_message)
        logger.info(f"Refused to user @{username}")


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    refuse_message: str = "",
    allowed_users: dict = {},
) -> None:
    username = update.effective_user.username
    if username not in allowed_users:
        await update.effective_message.reply_text(refuse_message)
        logger.info(f"Refused to user @{username}")
        return
    await update.effective_message.reply_text(
        HELP_MESSAGE,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def cancel_workflow(update, context):
    user_id = update.effective_user.id
    had_state = any(
        user_id in states
        for states in (manual_states, delete_states, card_states, edit_states)
    )
    manual_states.pop(user_id, None)
    delete_states.pop(user_id, None)
    card_states.pop(user_id, None)
    edit_states.pop(user_id, None)
    message = (
        "Текущее действие отменено." if had_state else "Активных действий нет."
    )
    await update.effective_message.reply_text(
        message, reply_markup=main_menu_keyboard()
    )


def format_cashback_list(rows, cards, category_emojis=None):
    # Group rows by (person, bank)
    groups = {}
    for row in rows:
        key = (row["Person"], row["Bank"])
        groups.setdefault(key, []).append(row)

    def sort_key(item):
        (person, bank), _ = item
        bank_idx = (
            BANK_ORDER.index(bank) if bank in BANK_ORDER else len(BANK_ORDER)
        )
        return (person or "", bank_idx, bank or "")

    blocks = []
    for (person, bank), group in sorted(groups.items(), key=sort_key):
        header = f"👤 {person or '—'} · {bank_label(bank)}"
        lines = [
            header,
            "",
            format_cards_line(card_numbers(cards, person, bank)),
            "",
        ]
        for i, row in enumerate(group, 1):
            category = category_label(row["Category"], category_emojis, row)
            lines.append(f"{i}. {category} — {format_percent(row['Percent'])}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def merge_options(*groups, limit=8):
    result = []
    for group in groups:
        for value in group:
            value = str(value).strip()
            if value and value not in result:
                result.append(value)
            if len(result) == limit:
                return result
    return result


async def begin_manual_add(
    update,
    context,
    pipeline,
    allowed_users,
    refuse_message,
):
    username = update.effective_user.username
    if username not in allowed_users:
        await update.effective_message.reply_text(refuse_message)
        return

    state = ManualAddState(
        person=allowed_users[username],
        date=selected_month(context),
    )
    state.options = pipeline.db.get_reference_values("Bank")
    delete_states.pop(update.effective_user.id, None)
    card_states.pop(update.effective_user.id, None)
    edit_states.pop(update.effective_user.id, None)
    manual_states[update.effective_user.id] = state
    await update.effective_message.reply_text(
        f"Выберите банк или напишите его название.\n"
        f"Месяц записи: {state.date[:7]}",
        reply_markup=option_keyboard(
            "manual:bank",
            state.options,
            labels=[bank_label(value) for value in state.options],
        ),
    )


def category_options(pipeline, state):
    rows = pipeline.db.get_all_rows()
    personal = frequent_values(
        rows,
        "Category",
        Person=state.person,
        Bank=state.bank,
    )
    bank = frequent_values(rows, "Category", Bank=state.bank)
    all_categories = pipeline.db.get_unique_categories()
    return merge_options(personal, bank, all_categories)


def category_option_labels(pipeline, options):
    emojis = pipeline.db.get_category_emojis()
    return [category_label(value, emojis) for value in options]


def percent_options(pipeline, state):
    rows = pipeline.db.get_all_rows()
    frequent = frequent_values(
        rows,
        "Percent",
        Person=state.person,
        Bank=state.bank,
        Category=state.category,
    )
    return merge_options(frequent, (1, 1.5, 2, 3, 5, 10, 15), limit=8)


async def set_manual_bank(update, state, value, pipeline):
    state.bank = canonical_bank(value)
    if not state.bank:
        await update.effective_message.reply_text("Название банка пустое.")
        return
    state.stage = "category"
    state.options = category_options(pipeline, state)
    await update.effective_message.reply_text(
        "Выберите частую категорию или напишите свою:",
        reply_markup=option_keyboard(
            "manual:category",
            state.options,
            back=True,
            labels=category_option_labels(pipeline, state.options),
        ),
    )


async def set_manual_category(update, state, value, pipeline):
    state.category = str(value).strip()
    if not state.category:
        await update.effective_message.reply_text("Название категории пустое.")
        return
    state.emoji = pipeline.db.get_category_emojis().get(
        state.category, category_emoji(state.category)
    )
    state.stage = "percent"
    state.options = percent_options(pipeline, state)
    await update.effective_message.reply_text(
        "Выберите процент или напишите число (например, 1,5):",
        reply_markup=option_keyboard(
            "manual:percent",
            state.options,
            columns=4,
            back=True,
        ),
    )


async def set_manual_percent(update, state, value):
    try:
        state.percent = parse_percent(value)
    except (TypeError, ValueError):
        await update.effective_message.reply_text(
            "Процент не распознан. Напишите число от 0 до 100, например 1,5."
        )
        return
    state.stage = "confirm"
    await update.effective_message.reply_text(
        "Проверьте запись:\n"
        f"• пользователь: {state.person}\n"
        f"• банк: {bank_label(state.bank)}\n"
        f"• категория: {category_label(state.category, row={'Emoji': state.emoji})}\n"
        f"• процент: {format_percent(state.percent)}\n"
        f"• месяц: {state.date[:7]}",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Сохранить", callback_data="manual:save"
                    ),
                    InlineKeyboardButton(
                        "❌ Отмена", callback_data="manual:cancel"
                    ),
                ],
                [InlineKeyboardButton("⬅️ Назад", callback_data="manual:back")],
            ]
        ),
    )


async def handle_manual_text(update, context, pipeline):
    state = manual_states.get(update.effective_user.id)
    if not state:
        return False
    value = update.effective_message.text
    if state.stage == "bank":
        await set_manual_bank(update, state, value, pipeline)
    elif state.stage == "category":
        await set_manual_category(update, state, value, pipeline)
    elif state.stage == "percent":
        await set_manual_percent(update, state, value)
    else:
        await update.effective_message.reply_text(
            "Используйте кнопки «Сохранить» или «Отмена»."
        )
    return True


async def save_manual_cashback(update, state, pipeline):
    query = update.callback_query
    if state.stage != "confirm":
        await query.message.reply_text("Сначала заполните все поля.")
        return
    row = {
        "Category": state.category,
        "Emoji": state.emoji,
        "Percent": state.percent,
        "Bank": state.bank,
        "Person": state.person,
        "Date": state.date,
    }
    try:
        saved, duplicates = pipeline.save_rows_to_database([row])
    except Exception:
        logger.error(
            "Error saving manual cashback for user %s",
            update.effective_user.id,
        )
        await query.message.reply_text("Не удалось сохранить запись.")
        return
    manual_states.pop(update.effective_user.id, None)
    await query.message.reply_text(
        save_result_message(saved, duplicates),
        reply_markup=main_menu_keyboard(),
    )


async def select_manual_option(update, state, action, pipeline):
    query = update.callback_query
    parts = action.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        return
    stage, index_text = parts
    index = int(index_text)
    if stage != state.stage or index >= len(state.options):
        await query.message.reply_text("Этот вариант уже неактуален.")
        return
    value = state.options[index]
    if stage == "bank":
        await set_manual_bank(update, state, value, pipeline)
    elif stage == "category":
        await set_manual_category(update, state, value, pipeline)
    elif stage == "percent":
        await set_manual_percent(update, state, value)


async def handle_manual_callback(update, context, pipeline):
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    if action == "cancel":
        manual_states.pop(update.effective_user.id, None)
        await query.message.reply_text(
            "Добавление отменено.",
            reply_markup=main_menu_keyboard(),
        )
        return
    state = manual_states.get(update.effective_user.id)
    if not state:
        await query.message.reply_text("Сессия добавления уже завершена.")
        return
    if action == "back":
        if state.stage == "confirm":
            state.stage = "percent"
            state.options = percent_options(pipeline, state)
            await query.message.reply_text(
                "Выберите процент или напишите число:",
                reply_markup=option_keyboard(
                    "manual:percent",
                    state.options,
                    columns=4,
                    back=True,
                ),
            )
        elif state.stage == "percent":
            state.stage = "category"
            state.options = category_options(pipeline, state)
            await query.message.reply_text(
                "Выберите категорию или напишите свою:",
                reply_markup=option_keyboard(
                    "manual:category",
                    state.options,
                    back=True,
                    labels=category_option_labels(pipeline, state.options),
                ),
            )
        elif state.stage == "category":
            state.stage = "bank"
            state.options = pipeline.db.get_reference_values("Bank")
            await query.message.reply_text(
                "Выберите банк или напишите его название:",
                reply_markup=option_keyboard(
                    "manual:bank",
                    state.options,
                    labels=[bank_label(value) for value in state.options],
                ),
            )
        return
    if action == "save":
        await save_manual_cashback(update, state, pipeline)
        return
    await select_manual_option(update, state, action, pipeline)


def delete_people(state):
    return sorted({row["Person"] for _, row in state.rows if row["Person"]})


def delete_banks(state):
    banks = {
        row["Bank"]
        for _, row in state.rows
        if row["Person"] == state.person and row["Bank"]
    }
    return sorted(
        banks,
        key=lambda bank: (
            BANK_ORDER.index(bank) if bank in BANK_ORDER else len(BANK_ORDER),
            bank,
        ),
    )


def delete_records(state):
    rows = [
        (row_number, row)
        for row_number, row in state.rows
        if row["Person"] == state.person and row["Bank"] == state.bank
    ]
    return sorted(
        rows,
        key=lambda item: (
            str(item[1].get("Category", "")),
            float(item[1].get("Percent") or 0),
            item[0],
        ),
    )


async def show_delete_people(message, state):
    state.stage = "person"
    state.options = delete_people(state)
    await message.reply_text(
        f"🗑 Удаление записи · месяц {state.date[:7]}\n"
        "Выберите пользователя:",
        reply_markup=option_keyboard("delete:person", state.options, columns=2),
    )


async def show_delete_banks(message, state):
    state.stage = "bank"
    state.options = delete_banks(state)
    await message.reply_text(
        f"Пользователь: {state.person}\nВыберите банк:",
        reply_markup=option_keyboard(
            "delete:bank",
            state.options,
            columns=2,
            back=True,
            labels=[bank_label(value) for value in state.options],
        ),
    )


async def show_delete_records(message, state):
    state.stage = "record"
    state.options = delete_records(state)
    labels = [
        f"{category_label(row['Category'], state.category_emojis, row)} · "
        f"{format_percent(row['Percent'])}"
        for _, row in state.options
    ]
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"delete:record:{index}")]
        for index, label in enumerate(labels)
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                f"🗑 Удалить все ({len(state.options)})",
                callback_data="delete:all",
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="delete:back"),
            InlineKeyboardButton("❌ Отмена", callback_data="delete:cancel"),
        ]
    )
    await message.reply_text(
        f"{state.person} · {bank_label(state.bank)}\n"
        "Выберите одну запись или удалите все категории банка:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_delete_confirmation(message, state):
    _, row = state.record
    state.bulk = False
    state.stage = "confirm"
    await message.reply_text(
        "<b>Удалить эту запись?</b>\n\n"
        f"👤 {html.escape(str(row['Person']))}\n"
        f"{html.escape(bank_label(row['Bank']))}\n"
        f"{html.escape(category_label(row['Category'], state.category_emojis, row))}\n"
        f"💳 {html.escape(format_percent(row['Percent']))}\n"
        f"📅 {html.escape(str(row['Date'])[:7])}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🗑 Удалить", callback_data="delete:confirm"
                    ),
                    InlineKeyboardButton(
                        "❌ Отмена", callback_data="delete:cancel"
                    ),
                ],
                [InlineKeyboardButton("⬅️ Назад", callback_data="delete:back")],
            ]
        ),
    )


async def show_bulk_delete_confirmation(message, state):
    state.bulk = True
    state.stage = "confirm"
    records = delete_records(state)
    lines = [
        "<b>Удалить все категории этого банка?</b>",
        "",
        f"👤 {html.escape(str(state.person))}",
        f"{html.escape(bank_label(state.bank))}",
        f"📅 {html.escape(state.date[:7])}",
        f"Записей: {len(records)}",
        "",
    ]
    lines.extend(
        f"• {html.escape(category_label(row['Category'], state.category_emojis, row))}"
        f" — {html.escape(format_percent(row['Percent']))}"
        for _, row in records
    )
    await message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"🗑 Удалить все ({len(records)})",
                        callback_data="delete:confirm",
                    ),
                    InlineKeyboardButton(
                        "❌ Отмена", callback_data="delete:cancel"
                    ),
                ],
                [InlineKeyboardButton("⬅️ Назад", callback_data="delete:back")],
            ]
        ),
    )


async def begin_delete(
    update,
    context,
    pipeline,
    allowed_users,
    refuse_message,
):
    username = update.effective_user.username
    if username not in allowed_users:
        await update.effective_message.reply_text(refuse_message)
        return
    date = selected_month(context)
    rows = pipeline.db.get_month_rows_with_indices(date)
    if not rows:
        await update.effective_message.reply_text(
            f"За {date[:7]} записей нет.", reply_markup=main_menu_keyboard()
        )
        return
    manual_states.pop(update.effective_user.id, None)
    card_states.pop(update.effective_user.id, None)
    edit_states.pop(update.effective_user.id, None)
    state = DeleteState(date, rows)
    state.category_emojis = pipeline.db.get_category_emojis()
    delete_states[update.effective_user.id] = state
    await show_delete_people(update.effective_message, state)


async def go_back_in_delete(message, state):
    if state.stage == "confirm":
        state.bulk = False
        await show_delete_records(message, state)
    elif state.stage == "record":
        await show_delete_banks(message, state)
    elif state.stage == "bank":
        await show_delete_people(message, state)


async def confirm_delete(message, user_id, state, pipeline):
    if state.stage != "confirm" or (not state.record and not state.bulk):
        await message.reply_text("Сначала выберите запись.")
        return
    try:
        if state.bulk:
            records = delete_records(state)
            deleted_count = pipeline.db.delete_rows(records)
        else:
            row_number, row = state.record
            pipeline.db.delete_row(row_number, row)
            deleted_count = 1
    except ValueError:
        logger.warning(
            "Cashback row changed before deletion for user %s", user_id
        )
        delete_states.pop(user_id, None)
        await message.reply_text(
            "Запись уже изменилась или была удалена. Начните удаление заново.",
            reply_markup=main_menu_keyboard(),
        )
        return
    except Exception:
        logger.error("Error deleting cashback for user %s", user_id)
        await message.reply_text("Не удалось удалить запись.")
        return
    delete_states.pop(user_id, None)
    await message.reply_text(
        (
            f"✅ Удалено записей: {deleted_count}."
            if deleted_count != 1
            else "✅ Запись удалена."
        ),
        reply_markup=main_menu_keyboard(),
    )


async def select_delete_option(message, action, state):
    parts = action.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        return
    stage, index_text = parts
    index = int(index_text)
    if stage != state.stage or index >= len(state.options):
        await message.reply_text("Этот вариант уже неактуален.")
        return
    value = state.options[index]
    if stage == "person":
        state.person = value
        await show_delete_banks(message, state)
    elif stage == "bank":
        state.bank = value
        await show_delete_records(message, state)
    elif stage == "record":
        state.record = value
        await show_delete_confirmation(message, state)


async def handle_delete_callback(update, context, pipeline):
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    user_id = update.effective_user.id
    if action == "cancel":
        delete_states.pop(user_id, None)
        await query.message.reply_text(
            "Удаление отменено.", reply_markup=main_menu_keyboard()
        )
        return
    state = delete_states.get(user_id)
    if not state:
        await query.message.reply_text("Сессия удаления уже завершена.")
        return
    if action == "back":
        await go_back_in_delete(query.message, state)
        return
    if action == "all":
        if state.stage != "record" or not state.options:
            await query.message.reply_text("Этот вариант уже неактуален.")
            return
        await show_bulk_delete_confirmation(query.message, state)
        return
    if action == "confirm":
        await confirm_delete(query.message, user_id, state, pipeline)
        return
    await select_delete_option(query.message, action, state)


async def show_card_modes(message):
    await message.reply_text(
        "💳 Управление картами\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "➕ Добавить карту", callback_data="cards:add"
                    ),
                    InlineKeyboardButton(
                        "🗑 Удалить карту", callback_data="cards:delete"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "❌ Закрыть", callback_data="cards:cancel"
                    )
                ],
            ]
        ),
    )


async def begin_cards(
    update,
    context,
    pipeline,
    allowed_users,
    refuse_message,
):
    username = update.effective_user.username
    if username not in allowed_users:
        await update.effective_message.reply_text(refuse_message)
        return
    user_id = update.effective_user.id
    manual_states.pop(user_id, None)
    delete_states.pop(user_id, None)
    edit_states.pop(user_id, None)
    card_states[user_id] = CardState("choose")
    await show_card_modes(update.effective_message)


def card_people_options(state, pipeline, allowed_users):
    if state.action == "delete":
        return sorted({row["Person"] for _, row in state.rows})
    people = set(allowed_users.values())
    people.update(pipeline.db.get_reference_values("Person"))
    return sorted(person for person in people if person)


def card_bank_options(state, pipeline):
    if state.action == "delete":
        banks = {
            row["Bank"]
            for _, row in state.rows
            if row["Person"] == state.person
        }
    else:
        banks = set(pipeline.db.get_reference_values("Bank"))
    return sorted(
        (bank for bank in banks if bank),
        key=lambda bank: (
            BANK_ORDER.index(bank) if bank in BANK_ORDER else len(BANK_ORDER),
            bank,
        ),
    )


def card_record_options(state):
    return sorted(
        (
            (row_number, row)
            for row_number, row in state.rows
            if row["Person"] == state.person and row["Bank"] == state.bank
        ),
        key=lambda item: (item[1]["Card"], item[0]),
    )


async def show_card_people(message, state, pipeline, allowed_users):
    state.stage = "person"
    state.options = card_people_options(state, pipeline, allowed_users)
    await message.reply_text(
        "Выберите пользователя:",
        reply_markup=option_keyboard("cards:person", state.options, columns=2),
    )


async def show_card_banks(message, state, pipeline):
    state.stage = "bank"
    state.options = card_bank_options(state, pipeline)
    await message.reply_text(
        f"Пользователь: {state.person}\nВыберите банк:",
        reply_markup=option_keyboard(
            "cards:bank",
            state.options,
            columns=2,
            back=True,
            labels=[bank_label(value) for value in state.options],
        ),
    )


async def ask_card_number(message, state):
    state.stage = "number"
    await message.reply_text(
        f"{state.person} · {bank_label(state.bank)}\n"
        "Напишите последние четыре цифры карты, например 1234.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⬅️ Назад", callback_data="cards:back"
                    ),
                    InlineKeyboardButton(
                        "❌ Отмена", callback_data="cards:cancel"
                    ),
                ]
            ]
        ),
    )


async def show_card_records(message, state):
    state.stage = "card"
    state.options = card_record_options(state)
    await message.reply_text(
        f"{state.person} · {bank_label(state.bank)}\n"
        "Выберите карту для удаления:",
        reply_markup=option_keyboard(
            "cards:card",
            state.options,
            columns=2,
            back=True,
            labels=[f"Карта {row['Card']}" for _, row in state.options],
        ),
    )


async def show_card_confirmation(message, state):
    deleting = state.action == "delete"
    state.stage = "confirm"
    title = "Удалить эту карту?" if deleting else "Добавить эту карту?"
    action = "🗑 Удалить" if deleting else "✅ Добавить"
    await message.reply_text(
        f"<b>{title}</b>\n\n"
        f"👤 {html.escape(str(state.person))}\n"
        f"{html.escape(bank_label(state.bank))}\n"
        f"💳 •••• {html.escape(state.card)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(action, callback_data="cards:confirm"),
                    InlineKeyboardButton(
                        "❌ Отмена", callback_data="cards:cancel"
                    ),
                ],
                [InlineKeyboardButton("⬅️ Назад", callback_data="cards:back")],
            ]
        ),
    )


async def handle_card_text(update, state):
    if state.stage != "number" or state.action != "add":
        await update.effective_message.reply_text(
            "В управлении картами используйте кнопки. "
            "Номер вводится только на шаге добавления карты."
        )
        return
    try:
        state.card = parse_card_last4(update.effective_message.text)
    except ValueError:
        await update.effective_message.reply_text(
            "Нужно ввести ровно четыре цифры, например 0123."
        )
        return
    await show_card_confirmation(update.effective_message, state)


async def confirm_card_action(message, user_id, state, pipeline):
    try:
        if state.action == "add":
            created = pipeline.db.add_card(state.person, state.bank, state.card)
            result = (
                "✅ Карта добавлена."
                if created
                else "ℹ️ Такая карта уже добавлена."
            )
        else:
            row_number, row = state.record
            pipeline.db.delete_card(row_number, row)
            result = "✅ Карта удалена."
    except ValueError:
        card_states.pop(user_id, None)
        await message.reply_text(
            "Запись карты уже изменилась. Начните заново.",
            reply_markup=main_menu_keyboard(),
        )
        return
    except Exception:
        logger.error("Error updating cards for user %s", user_id)
        await message.reply_text("Не удалось обновить карты.")
        return
    card_states.pop(user_id, None)
    await message.reply_text(result, reply_markup=main_menu_keyboard())


async def go_back_in_cards(message, state, pipeline, allowed_users):
    if state.stage == "confirm":
        if state.action == "add":
            await ask_card_number(message, state)
        else:
            await show_card_records(message, state)
    elif state.stage in ("number", "card"):
        await show_card_banks(message, state, pipeline)
    elif state.stage == "bank":
        await show_card_people(message, state, pipeline, allowed_users)


async def select_card_option(message, action, state, pipeline):
    parts = action.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        return
    stage, index_text = parts
    index = int(index_text)
    if stage != state.stage or index >= len(state.options):
        await message.reply_text("Этот вариант уже неактуален.")
        return
    value = state.options[index]
    if stage == "person":
        state.person = value
        await show_card_banks(message, state, pipeline)
    elif stage == "bank":
        state.bank = value
        if state.action == "add":
            await ask_card_number(message, state)
        else:
            await show_card_records(message, state)
    elif stage == "card":
        state.record = value
        state.card = value[1]["Card"]
        await show_card_confirmation(message, state)


async def handle_cards_callback(
    update,
    context,
    pipeline,
    allowed_users,
    refuse_message,
):
    query = update.callback_query
    await query.answer()
    username = update.effective_user.username
    if username not in allowed_users:
        await query.message.reply_text(refuse_message)
        return
    action = query.data.split(":", 1)[1]
    user_id = update.effective_user.id
    if action == "cancel":
        card_states.pop(user_id, None)
        await query.message.reply_text(
            "Управление картами закрыто.", reply_markup=main_menu_keyboard()
        )
        return
    if action in ("add", "delete"):
        rows = (
            pipeline.db.get_cards_with_indices() if action == "delete" else []
        )
        if action == "delete" and not rows:
            await query.message.reply_text(
                "Добавленных карт пока нет.",
                reply_markup=main_menu_keyboard(),
            )
            return
        state = CardState(action, rows)
        card_states[user_id] = state
        await show_card_people(query.message, state, pipeline, allowed_users)
        return
    state = card_states.get(user_id)
    if not state:
        await query.message.reply_text("Сессия управления картами завершена.")
        return
    if action == "back":
        await go_back_in_cards(query.message, state, pipeline, allowed_users)
    elif action == "confirm":
        await confirm_card_action(query.message, user_id, state, pipeline)
    else:
        await select_card_option(query.message, action, state, pipeline)


def month_keyboard():
    buttons = [
        InlineKeyboardButton(
            "🔄 Авто: после 25-го → следующий", callback_data="month:auto"
        )
    ]
    buttons.extend(
        InlineKeyboardButton(value[:7], callback_data=f"month:{value[:7]}")
        for value in (month_start(offset) for offset in range(1, -12, -1))
    )
    rows = [[buttons[0]]]
    rows.extend(
        buttons[index : index + 3] for index in range(1, len(buttons), 3)
    )
    return InlineKeyboardMarkup(rows)


async def choose_month(update, context, allowed_users, refuse_message):
    username = update.effective_user.username
    if username not in allowed_users:
        await update.effective_message.reply_text(refuse_message)
        return
    user_id = update.effective_user.id
    manual_states.pop(user_id, None)
    delete_states.pop(user_id, None)
    card_states.pop(user_id, None)
    edit_states.pop(user_id, None)
    await update.effective_message.reply_text(
        f"Рабочий месяц: {selected_month_label(context)}\n"
        "Он используется для фото, ручных записей, списка, поиска и удаления.\n"
        "В авторежиме до 25-го включительно выбирается текущий месяц, "
        "с 26-го — следующий.",
        reply_markup=month_keyboard(),
    )


async def handle_month_callback(update, context):
    query = update.callback_query
    await query.answer()
    value = query.data.split(":", 1)[1]
    if value == "auto":
        context.user_data.pop("target_month", None)
    else:
        context.user_data["target_month"] = f"{value}-01"
    await query.message.reply_text(
        f"✅ Рабочий месяц: {selected_month_label(context)}",
        reply_markup=main_menu_keyboard(),
    )


async def search_cashbacks(
    update,
    context,
    pipeline,
    allowed_users,
    refuse_message,
):
    username = update.effective_user.username
    if username not in allowed_users:
        await update.effective_message.reply_text(refuse_message)
        return
    month = selected_month(context)
    rows = pipeline.db.get_month_rows(month)
    cards = pipeline.db.get_cards()
    category_emojis = pipeline.db.get_category_emojis()
    matches = search_category_rows(rows, update.effective_message.text)
    if not matches:
        await update.effective_message.reply_text(
            f"За {month[:7]} похожих категорий не найдено."
        )
        return
    lines = [f"Найдено за {month[:7]}:"]
    for row in matches:
        numbers = card_numbers(cards, row["Person"], row["Bank"])
        lines.append(
            f"• {category_label(row['Category'], category_emojis, row)} — "
            f"{row['Person']}, {bank_label(row['Bank'])}, "
            f"{format_percent(row['Percent'])}\n"
            f"  {format_cards_line(numbers)}"
        )
    await update.effective_message.reply_text("\n".join(lines))


async def handle_text_message(
    update,
    context,
    pipeline,
    allowed_users,
    refuse_message,
):
    edit_state = edit_states.get(update.effective_user.id)
    if edit_state and edit_state.current_edit:
        await handle_edit_message(update, context, pipeline)
        return
    if update.effective_user.id in delete_states:
        await update.effective_message.reply_text(
            "В режиме удаления используйте только кнопки. "
            "Для выхода нажмите «Отмена» или отправьте /cancel."
        )
        return
    card_state = card_states.get(update.effective_user.id)
    if card_state:
        await handle_card_text(update, card_state)
        return
    if await handle_manual_text(update, context, pipeline):
        return
    await search_cashbacks(
        update,
        context,
        pipeline,
        allowed_users,
        refuse_message,
    )


async def list_cashbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pipeline,
    refuse_message: str = "",
    empty_message: str = "На выбранный месяц кэшбэков пока нет 🤷",
    not_ok_message: str = "Не удалось загрузить список.",
    allowed_users: dict = {},
) -> None:
    username = update.effective_user.username
    if username not in allowed_users:
        await update.effective_message.reply_text(refuse_message)
        logger.info(f"Refused to user @{username}")
        return

    logger.info(f"List command for user @{username}")
    try:
        rows = pipeline.db.get_month_rows(selected_month(context))
        if not rows:
            await update.effective_message.reply_text(empty_message)
            return

        cards = pipeline.db.get_cards()
        category_emojis = pipeline.db.get_category_emojis()
        message = (
            f"Кэшбэки за {selected_month(context)[:7]}:\n\n"
            f"{format_cashback_list(rows, cards, category_emojis)}"
        )
        await update.effective_message.reply_text(
            message, reply_markup=main_menu_keyboard()
        )
        logger.info(f"Sent cashback list to user @{username}")
    except Exception:
        logger.error("Error building cashback list for @%s", username)
        await update.effective_message.reply_text(not_ok_message)


async def handle_menu_callback(
    update,
    context,
    pipeline,
    allowed_users,
    refuse_message,
    empty_message,
    not_ok_message,
):
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    if action == "add":
        await begin_manual_add(
            update,
            context,
            pipeline,
            allowed_users,
            refuse_message,
        )
    elif action == "delete":
        await begin_delete(
            update,
            context,
            pipeline,
            allowed_users,
            refuse_message,
        )
    elif action == "cards":
        await begin_cards(
            update,
            context,
            pipeline,
            allowed_users,
            refuse_message,
        )
    elif action == "month":
        await choose_month(update, context, allowed_users, refuse_message)
    elif action == "list":
        await list_cashbacks(
            update,
            context,
            pipeline,
            refuse_message=refuse_message,
            empty_message=empty_message,
            not_ok_message=not_ok_message,
            allowed_users=allowed_users,
        )


async def handle_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pipeline,
    images_path: str,
    refuse_message: str = "",
    processing_message: str = "Скриншот обрабатывается… 🔄",
    ok_message: str = "Категории распознаны.",
    continue_message: str = "Можно отправить следующий скриншот 📨",
    not_ok_message: str = "Не удалось обработать скриншот.",
    allowed_users: dict = {},
) -> None:
    username = update.effective_user.username
    if username not in allowed_users:
        await update.message.reply_text(refuse_message)
        logger.info(f"Refused to user @{username}")
        return
    db_username = allowed_users[username]

    await update.message.reply_text(processing_message)

    photo_file = await update.message.photo[-1].get_file()
    image_path = os.path.join(
        images_path, f"{username}_{photo_file.file_unique_id}.jpg"
    )
    await photo_file.download_to_drive(image_path)
    logger.info(f"Recieved image {image_path}")

    try:
        rows = pipeline(
            image_path,
            db_username,
            date=selected_month(context),
        )

        if not rows:
            await update.message.reply_text(
                "На скриншоте категории не распознаны. "
                "Попробуйте отправить более чёткое изображение."
            )
            return

        # Store edit state
        manual_states.pop(update.effective_user.id, None)
        delete_states.pop(update.effective_user.id, None)
        card_states.pop(update.effective_user.id, None)
        edit_states[update.effective_user.id] = EditState(
            rows, image_path, db_username
        )

        await update.message.reply_text(
            format_rows_preview(
                rows, category_emojis=pipeline.db.get_category_emojis()
            ),
            parse_mode="HTML",
            reply_markup=create_edit_keyboard(rows),
        )

        logger.info(f"Processed image {image_path}")

    except Exception:
        logger.error("Error during processing of %s", image_path)
        await update.message.reply_text(not_ok_message)


async def configure_bot_commands(application):
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Открыть меню"),
            BotCommand("help", "Что умеет бот"),
            BotCommand("add", "Добавить категорию вручную"),
            BotCommand("delete", "Удалить запись"),
            BotCommand("cards", "Управление картами"),
            BotCommand("month", "Выбрать рабочий месяц"),
            BotCommand("list", "Показать кэшбэки выбранного месяца"),
            BotCommand("cancel", "Отменить текущее действие"),
        ]
    )


def run_bot(cfg):
    persistence = PicklePersistence(
        filepath=cfg["bot"].get("state_path", "data/bot_state.pkl")
    )
    application = (
        Application.builder()
        .token(cfg["bot"]["token"])
        .persistence(persistence)
        .post_init(configure_bot_commands)
        .build()
    )

    pipe = Pipeline(cfg)
    logger.info("Pipeline initialized")
    allowed_users = {
        user["tg_username"]: user["db_username"] for user in cfg["bot"]["users"]
    }

    application.add_handler(
        CommandHandler(
            "start",
            partial(
                start,
                start_message=cfg["bot"]["messages"]["start_message"],
                refuse_message=cfg["bot"]["messages"]["refuse_message"],
                allowed_users=allowed_users,
            ),
        )
    )
    application.add_handler(
        CommandHandler(
            "help",
            partial(
                help_command,
                refuse_message=cfg["bot"]["messages"]["refuse_message"],
                allowed_users=allowed_users,
            ),
        )
    )
    application.add_handler(
        CommandHandler(
            "add",
            partial(
                begin_manual_add,
                pipeline=pipe,
                allowed_users=allowed_users,
                refuse_message=cfg["bot"]["messages"]["refuse_message"],
            ),
        )
    )
    application.add_handler(
        CommandHandler(
            "delete",
            partial(
                begin_delete,
                pipeline=pipe,
                allowed_users=allowed_users,
                refuse_message=cfg["bot"]["messages"]["refuse_message"],
            ),
        )
    )
    application.add_handler(
        CommandHandler(
            "cards",
            partial(
                begin_cards,
                pipeline=pipe,
                allowed_users=allowed_users,
                refuse_message=cfg["bot"]["messages"]["refuse_message"],
            ),
        )
    )
    application.add_handler(
        CommandHandler(
            "month",
            partial(
                choose_month,
                allowed_users=allowed_users,
                refuse_message=cfg["bot"]["messages"]["refuse_message"],
            ),
        )
    )
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            partial(
                handle_image,
                pipeline=pipe,
                images_path=cfg["bot"]["images_path"],
                refuse_message=cfg["bot"]["messages"]["refuse_message"],
                processing_message=cfg["bot"]["messages"]["processing_message"],
                ok_message=cfg["bot"]["messages"]["ok_message"],
                continue_message=cfg["bot"]["messages"]["continue_message"],
                not_ok_message=cfg["bot"]["messages"]["not_ok_message"],
                allowed_users=allowed_users,
            ),
        )
    )
    application.add_handler(
        CommandHandler(
            "list",
            partial(
                list_cashbacks,
                pipeline=pipe,
                refuse_message=cfg["bot"]["messages"]["refuse_message"],
                empty_message=cfg["bot"]["messages"].get(
                    "empty_list_message",
                    "На выбранный месяц кэшбэков пока нет 🤷",
                ),
                not_ok_message=cfg["bot"]["messages"]["not_ok_message"],
                allowed_users=allowed_users,
            ),
        )
    )
    application.add_handler(CommandHandler("cancel", cancel_workflow))
    application.add_handler(
        CallbackQueryHandler(
            partial(handle_manual_callback, pipeline=pipe),
            pattern=r"^manual:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            partial(handle_delete_callback, pipeline=pipe),
            pattern=r"^delete:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            partial(
                handle_cards_callback,
                pipeline=pipe,
                allowed_users=allowed_users,
                refuse_message=cfg["bot"]["messages"]["refuse_message"],
            ),
            pattern=r"^cards:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(handle_month_callback, pattern=r"^month:")
    )
    application.add_handler(
        CallbackQueryHandler(
            partial(
                handle_menu_callback,
                pipeline=pipe,
                allowed_users=allowed_users,
                refuse_message=cfg["bot"]["messages"]["refuse_message"],
                empty_message=cfg["bot"]["messages"].get(
                    "empty_list_message",
                    "На выбранный месяц кэшбэков пока нет 🤷",
                ),
                not_ok_message=cfg["bot"]["messages"]["not_ok_message"],
            ),
            pattern=r"^menu:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            partial(handle_edit_callback, pipeline=pipe),
            pattern=r"^(edit_|confirm_edit|cancel_edit)",
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            partial(
                handle_text_message,
                pipeline=pipe,
                allowed_users=allowed_users,
                refuse_message=cfg["bot"]["messages"]["refuse_message"],
            ),
        )
    )

    logger.info("STARTING BOT")
    application.run_polling()
