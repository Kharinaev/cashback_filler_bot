import logging
import os
from functools import partial

import telegram
from src.bot_helpers import (
    frequent_values,
    month_start,
    parse_percent,
    search_category_rows,
)
from src.pipe import Pipeline
from tabulate import tabulate
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


# Store edit states for each user
edit_states = {}
manual_states = {}


def create_edit_keyboard(rows):
    keyboard = []

    # Add "Edit Bank" button
    keyboard.append(
        [InlineKeyboardButton("Edit Bank", callback_data="edit_bank")]
    )

    # Add row-specific edit buttons
    for i, row in enumerate(rows, 1):
        keyboard.extend(
            [
                [
                    InlineKeyboardButton(
                        f"Edit Category {i}", callback_data=f"edit_category_{i}"
                    ),
                    InlineKeyboardButton(
                        f"Edit Percent {i}", callback_data=f"edit_percent_{i}"
                    ),
                ]
            ]
        )

    # Add confirm and cancel buttons
    keyboard.append(
        [
            InlineKeyboardButton("✅ Confirm", callback_data="confirm_edit"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_edit"),
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
        await query.message.reply_text(
            "No active edit session. Please start over."
        )
        return

    if query.data == "edit_bank":
        logger.info(f"User @{username} (ID: {user_id}) started bank edit")
        state.current_edit = ("bank", None)
        await query.message.reply_text("Please send the correct bank name.")

    elif query.data.startswith("edit_category_"):
        row_idx = int(query.data.split("_")[2]) - 1
        logger.info(
            f"User @{username} (ID: {user_id}) started category edit for row {row_idx + 1}"
        )
        state.current_edit = ("category", row_idx)
        await query.message.reply_text(
            f"Please send the correct category for row {row_idx + 1}."
        )

    elif query.data.startswith("edit_percent_"):
        row_idx = int(query.data.split("_")[2]) - 1
        logger.info(
            f"User @{username} (ID: {user_id}) started percent edit for row {row_idx + 1}"
        )
        state.current_edit = ("percent", row_idx)
        await query.message.reply_text(
            f"Please send the correct percentage for row {row_idx + 1}."
        )

    elif query.data == "confirm_edit":
        logger.info(f"User @{username} (ID: {user_id}) confirmed edits")
        # Save to Google Sheets
        pipeline = context.bot_data.get("pipeline")
        if pipeline:
            try:
                pipeline.save_rows_to_database(state.edited_rows)
                logger.info(
                    f"Successfully saved edited rows for user @{username} (ID: {user_id})"
                )
                await query.message.reply_text("✅ Changes saved successfully!")
            except Exception as e:
                logger.error(
                    f"Error saving changes for user @{username} (ID: {user_id}): {str(e)}"
                )
                await query.message.reply_text(
                    f"❌ Error saving changes: {str(e)}"
                )
        del edit_states[user_id]

    elif query.data == "cancel_edit":
        logger.info(f"User @{username} (ID: {user_id}) cancelled edits")
        await query.message.reply_text("❌ Edit cancelled.")
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
            new_percent = float(new_value)
            state.edited_rows[row_idx]["Percent"] = new_percent
            logger.info(
                f"User @{username} (ID: {user_id}) changed percent in row {row_idx + 1} from {old_value} to {new_percent}"
            )
        except ValueError:
            logger.warning(
                f"User @{username} (ID: {user_id}) sent invalid percentage value: {new_value}"
            )
            await update.message.reply_text(
                "Please send a valid number for the percentage."
            )
            return

    # Show updated table with enumeration
    table_data = []
    for i, row in enumerate(state.edited_rows, 1):
        row_data = {
            "#": i,
            "Category": row["Category"],
            "Percent": row["Percent"],
            "Bank": row["Bank"],
        }
        table_data.append(row_data)

    table = tabulate(
        table_data,
        headers="keys",
        tablefmt="simple",
    )

    await update.message.reply_text(
        f"Updated table:\n```\n{table}\n```",
        parse_mode=telegram.constants.ParseMode.MARKDOWN_V2,
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
                InlineKeyboardButton(
                    "📅 Выбрать месяц", callback_data="menu:month"
                )
            ],
        ]
    )


def selected_month(context):
    return context.user_data.get("target_month") or month_start()


def selected_month_label(context):
    value = context.user_data.get("target_month")
    return value[:7] if value else f"{month_start()[:7]} (авто)"


def option_keyboard(prefix, options, columns=2, cancel=True):
    buttons = [
        InlineKeyboardButton(str(option), callback_data=f"{prefix}:{index}")
        for index, option in enumerate(options)
    ]
    rows = [
        buttons[index : index + columns]
        for index in range(0, len(buttons), columns)
    ]
    if cancel:
        rows.append(
            [InlineKeyboardButton("❌ Отмена", callback_data="manual:cancel")]
        )
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
        await update.effective_message.reply_text(
            start_message,
            reply_markup=main_menu_keyboard(),
        )
        logger.info(f"Start command for user @{username}")
    else:
        await update.effective_message.reply_text(refuse_message)
        logger.info(f"Refused to user @{username}")


# Display name and color emoji for each bank
BANK_DISPLAY = {
    "Tinkoff": ("ТБанк", "🟡"),
    "Alfa": ("Альфа", "🔴"),
    "Ozon": ("Ozon", "🔵"),
}

# Order in which banks are shown within a person's cashbacks
BANK_ORDER = ["Tinkoff", "Alfa", "Ozon"]


def format_percent(percent):
    if percent in (None, ""):
        return ""
    if float(percent).is_integer():
        return f"{int(percent)}%"
    return f"{percent}%"


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
        bank_name, emoji = BANK_DISPLAY.get(bank, (bank or "—", ""))
        header = f"{person or '—'} {bank_name} {emoji}".rstrip()
        lines = [header]
        for i, row in enumerate(group, 1):
            category = row["Category"] or "—"
            lines.append(f"{i}. {category} {format_percent(row['Percent'])}")
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
    manual_states[update.effective_user.id] = state
    await update.effective_message.reply_text(
        f"Выберите банк или напишите его название.\n"
        f"Месяц записи: {state.date[:7]}",
        reply_markup=option_keyboard("manual:bank", state.options),
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
    state.bank = str(value).strip()
    if not state.bank:
        await update.effective_message.reply_text("Название банка пустое.")
        return
    state.stage = "category"
    state.options = category_options(pipeline, state)
    await update.effective_message.reply_text(
        "Выберите частую категорию или напишите свою:",
        reply_markup=option_keyboard("manual:category", state.options),
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
        ),
    )


async def set_manual_percent(update, state, value):
    try:
        state.percent = parse_percent(value)
    except (TypeError, ValueError):
        await update.effective_message.reply_text(
            "Не поняла процент. Напишите число от 0 до 100, например 1,5."
        )
        return
    state.stage = "confirm"
    await update.effective_message.reply_text(
        "Проверьте запись:\n"
        f"• пользователь: {state.person}\n"
        f"• банк: {state.bank}\n"
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
                ]
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
    except Exception as error:
        logger.error(
            "Error saving manual cashback for user %s: %s",
            update.effective_user.id,
            error,
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
    if action == "save":
        await save_manual_cashback(update, state, pipeline)
        return
    await select_manual_option(update, state, action, pipeline)


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
    await update.effective_message.reply_text(
        f"Рабочий месяц: {selected_month_label(context)}\n"
        "Он будет использоваться для фото и ручных записей.",
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
    pipeline,
    allowed_users,
    refuse_message,
):
    username = update.effective_user.username
    if username not in allowed_users:
        await update.effective_message.reply_text(refuse_message)
        return
    rows = pipeline.db.get_current_month_rows()
    matches = search_category_rows(rows, update.effective_message.text)
    if not matches:
        await update.effective_message.reply_text(
            "В текущем месяце похожих категорий не нашла."
        )
        return
    lines = ["Нашла в текущем месяце:"]
    for row in matches:
        lines.append(
            f"• {row['Category']} — {row['Person']}, {row['Bank']}, "
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
    if await handle_manual_text(update, context, pipeline):
        return
    await search_cashbacks(
        update,
        pipeline,
        allowed_users,
        refuse_message,
    )


async def list_cashbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pipeline,
    refuse_message: str = "",
    empty_message: str = "No cashbacks found.",
    not_ok_message: str = "not_ok",
    allowed_users: dict = {},
) -> None:
    username = update.effective_user.username
    if username not in allowed_users:
        await update.effective_message.reply_text(refuse_message)
        logger.info(f"Refused to user @{username}")
        return

    logger.info(f"List command for user @{username}")
    try:
        rows = pipeline.db.get_current_month_rows()
        if not rows:
            await update.effective_message.reply_text(empty_message)
            return

        message = format_cashback_list(rows)
        await update.effective_message.reply_text(message)
        logger.info(f"Sent cashback list to user @{username}")
    except Exception as e:
        logger.error(f"Error building cashback list for @{username} - {e}")
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
    processing_message: str = "processing",
    ok_message: str = "ok",
    continue_message: str = "continue",
    not_ok_message: str = "not_ok",
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

        # Create table with enumeration
        table_data = []
        for i, row in enumerate(rows, 1):
            row_data = {
                "#": i,
                "Category": row["Category"],
                "Percent": row["Percent"],
                "Bank": row["Bank"],
            }
            table_data.append(row_data)

        table = tabulate(
            table_data,
            headers="keys",
            tablefmt="simple",
        )
        await update.message.reply_text(ok_message)

        # Store edit state
        edit_states[update.effective_user.id] = EditState(
            rows, image_path, db_username
        )

        # Send table with edit buttons
        await update.message.reply_text(
            f"```\n{table}\n```",
            parse_mode=telegram.constants.ParseMode.MARKDOWN_V2,
            reply_markup=create_edit_keyboard(rows),
        )

        logger.info(f"Processed image {image_path}")
        await update.message.reply_text(continue_message)

    except Exception as e:
        logger.error(f"Error during processing of {image_path} - {e}")
        await update.message.reply_text(not_ok_message)


async def configure_bot_commands(application):
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Открыть меню"),
            BotCommand("add", "Добавить категорию вручную"),
            BotCommand("month", "Выбрать рабочий месяц"),
            BotCommand("list", "Показать кэшбэки текущего месяца"),
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
                    "empty_list_message", "На этот месяц кэшбэков пока нет 🤷"
                ),
                not_ok_message=cfg["bot"]["messages"]["not_ok_message"],
                allowed_users=allowed_users,
            ),
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            partial(handle_manual_callback, pipeline=pipe),
            pattern=r"^manual:",
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
                    "На этот месяц кэшбэков пока нет 🤷",
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
