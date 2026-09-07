import html
import logging
import os
from functools import partial

from src.bot_helpers import (
    canonical_bank,
    frequent_values,
    month_start,
    parse_percent,
    search_category_rows,
)
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


class ManualAddState:
    def __init__(self, person, date):
        self.person = person
        self.date = date
        self.stage = "bank"
        self.bank = None
        self.category = None
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
        self.options = []


# Store edit states for each user
edit_states = {}
manual_states = {}
delete_states = {}


# Display name and color emoji for each bank.
BANK_DISPLAY = {
    "Tinkoff": ("ТБанк", "🟡"),
    "Alfa": ("Альфа", "🔴"),
    "Ozon": ("Ozon", "🔵"),
}

# Order in which banks are shown within a person's cashbacks.
BANK_ORDER = ["Tinkoff", "Alfa", "Ozon"]


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


def format_rows_preview(rows, title="Проверьте распознанные категории"):
    lines = [f"<b>{html.escape(title)}</b>"]
    for index, row in enumerate(rows, 1):
        lines.extend(
            [
                "",
                f"<b>{index}. {html.escape(str(row.get('Category') or '—'))}</b>"
                f" — {html.escape(format_percent(row.get('Percent')))}",
                f"{html.escape(bank_label(row.get('Bank')))}",
            ]
        )
    return "\n".join(lines)


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
    update: Update, context: ContextTypes.DEFAULT_TYPE
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

    if query.data == "edit_bank":
        logger.info(f"User @{username} (ID: {user_id}) started bank edit")
        state.current_edit = ("bank", None)
        await query.message.reply_text(
            "Напишите правильное название банка. Оно применится ко всем строкам."
        )

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
        pipeline = context.bot_data.get("pipeline")
        if pipeline:
            try:
                for row in state.edited_rows:
                    for field in ("Category", "Person", "Bank"):
                        pipeline.db.ensure_reference_value(field, row[field])
                pipeline.save_rows_to_database(state.edited_rows)
                logger.info(
                    f"Successfully saved edited rows for user @{username} (ID: {user_id})"
                )
                await query.message.reply_text(
                    "✅ Категории сохранены.",
                    reply_markup=main_menu_keyboard(),
                )
            except Exception:
                logger.error(
                    "Error saving changes for user @%s (ID: %s)",
                    username,
                    user_id,
                )
                await query.message.reply_text(
                    "❌ Не удалось сохранить изменения."
                )
        del edit_states[user_id]

    elif query.data == "cancel_edit":
        logger.info(f"User @{username} (ID: {user_id}) cancelled edits")
        await query.message.reply_text(
            "Проверка отменена.", reply_markup=main_menu_keyboard()
        )
        del edit_states[user_id]


async def handle_edit_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
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
        new_value = canonical_bank(new_value)
        for row in state.edited_rows:
            row["Bank"] = new_value
        logger.info(
            f"User @{username} (ID: {user_id}) changed bank from '{old_value}' to '{new_value}'"
        )
    elif edit_type == "category":
        old_value = state.edited_rows[row_idx]["Category"]
        state.edited_rows[row_idx]["Category"] = new_value
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
        format_rows_preview(state.edited_rows, "Обновлённый результат"),
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
        ]
    )


def selected_month(context):
    return context.user_data.get("target_month") or month_start()


def selected_month_label(context):
    value = context.user_data.get("target_month")
    return value[:7] if value else f"{month_start()[:7]} (авто)"


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


async def cancel_workflow(update, context):
    user_id = update.effective_user.id
    had_state = any(
        user_id in states
        for states in (manual_states, delete_states, edit_states)
    )
    manual_states.pop(user_id, None)
    delete_states.pop(user_id, None)
    edit_states.pop(user_id, None)
    message = (
        "Текущее действие отменено." if had_state else "Активных действий нет."
    )
    await update.effective_message.reply_text(
        message, reply_markup=main_menu_keyboard()
    )


def format_cashback_list(rows):
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
        lines = [header]
        for i, row in enumerate(group, 1):
            category = row["Category"] or "—"
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
            "manual:category", state.options, back=True
        ),
    )


async def set_manual_category(update, state, value, pipeline):
    state.category = str(value).strip()
    if not state.category:
        await update.effective_message.reply_text("Название категории пустое.")
        return
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
        f"• категория: {state.category}\n"
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
        "Percent": state.percent,
        "Bank": state.bank,
        "Person": state.person,
        "Date": state.date,
    }
    try:
        for field in ("Category", "Person", "Bank"):
            pipeline.db.ensure_reference_value(field, row[field])
        pipeline.save_rows_to_database([row])
    except Exception:
        logger.error(
            "Error saving manual cashback for user %s",
            update.effective_user.id,
        )
        await query.message.reply_text("Не удалось сохранить запись.")
        return
    manual_states.pop(update.effective_user.id, None)
    await query.message.reply_text(
        "✅ Категория добавлена.",
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
                    "manual:category", state.options, back=True
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
        f"{row['Category']} · {format_percent(row['Percent'])}"
        for _, row in state.options
    ]
    await message.reply_text(
        f"{state.person} · {bank_label(state.bank)}\n"
        "Выберите запись для удаления:",
        reply_markup=option_keyboard(
            "delete:record",
            state.options,
            columns=1,
            back=True,
            labels=labels,
        ),
    )


async def show_delete_confirmation(message, state):
    _, row = state.record
    state.stage = "confirm"
    await message.reply_text(
        "<b>Удалить эту запись?</b>\n\n"
        f"👤 {html.escape(str(row['Person']))}\n"
        f"{html.escape(bank_label(row['Bank']))}\n"
        f"🏷 {html.escape(str(row['Category']))}\n"
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
    edit_states.pop(update.effective_user.id, None)
    state = DeleteState(date, rows)
    delete_states[update.effective_user.id] = state
    await show_delete_people(update.effective_message, state)


async def go_back_in_delete(message, state):
    if state.stage == "confirm":
        await show_delete_records(message, state)
    elif state.stage == "record":
        await show_delete_banks(message, state)
    elif state.stage == "bank":
        await show_delete_people(message, state)


async def confirm_delete(message, user_id, state, pipeline):
    if state.stage != "confirm" or not state.record:
        await message.reply_text("Сначала выберите запись.")
        return
    row_number, row = state.record
    try:
        pipeline.db.delete_row(row_number, row)
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
        "✅ Запись удалена.", reply_markup=main_menu_keyboard()
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
    if action == "confirm":
        await confirm_delete(query.message, user_id, state, pipeline)
        return
    await select_delete_option(query.message, action, state)


def month_keyboard():
    buttons = [
        InlineKeyboardButton(
            "🔄 Авто: текущий месяц", callback_data="month:auto"
        )
    ]
    buttons.extend(
        InlineKeyboardButton(value[:7], callback_data=f"month:{value[:7]}")
        for value in (month_start(-offset) for offset in range(12))
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
    edit_states.pop(user_id, None)
    await update.effective_message.reply_text(
        f"Рабочий месяц: {selected_month_label(context)}\n"
        "Он используется для фото, ручных записей, списка, поиска и удаления.",
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
    matches = search_category_rows(rows, update.effective_message.text)
    if not matches:
        await update.effective_message.reply_text(
            f"За {month[:7]} похожих категорий не найдено."
        )
        return
    lines = [f"Найдено за {month[:7]}:"]
    for row in matches:
        lines.append(
            f"• {row['Category']} — {row['Person']}, {bank_label(row['Bank'])}, "
            f"{format_percent(row['Percent'])}"
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
        await handle_edit_message(update, context)
        return
    if update.effective_user.id in delete_states:
        await update.effective_message.reply_text(
            "В режиме удаления используйте только кнопки. "
            "Для выхода нажмите «Отмена» или отправьте /cancel."
        )
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

        message = (
            f"Кэшбэки за {selected_month(context)[:7]}:\n\n"
            f"{format_cashback_list(rows)}"
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

        # Store pipeline in bot_data for access in handlers
        context.bot_data["pipeline"] = pipeline

        if not rows:
            await update.message.reply_text(
                "На скриншоте категории не распознаны. "
                "Попробуйте отправить более чёткое изображение."
            )
            return

        # Store edit state
        manual_states.pop(update.effective_user.id, None)
        delete_states.pop(update.effective_user.id, None)
        edit_states[update.effective_user.id] = EditState(
            rows, image_path, db_username
        )

        await update.message.reply_text(
            format_rows_preview(rows),
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
            BotCommand("add", "Добавить категорию вручную"),
            BotCommand("delete", "Удалить запись"),
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

    # Store pipeline in bot_data
    application.bot_data["pipeline"] = pipe

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
            handle_edit_callback,
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
