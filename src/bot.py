import logging
import os
from functools import partial

import telegram
from src.pipe import Pipeline
from tabulate import tabulate
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
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


# Store edit states for each user
edit_states = {}


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
        # Save to Notion DB
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


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    start_message: str = "",
    refuse_message: str = "",
    allowed_users: dict = {},
) -> None:
    username = update.effective_user.username
    if username in allowed_users:
        await update.message.reply_text(start_message)
        logger.info(f"Start command for user @{username}")
    else:
        await update.message.reply_text(refuse_message)
        logger.info(f"Refused to user @{username}")


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
        rows = pipeline(image_path, db_username)

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


def run_bot(cfg):
    application = Application.builder().token(cfg["bot"]["token"]).build()

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
                allowed_users=allowed_users.keys(),
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
    application.add_handler(CallbackQueryHandler(handle_edit_callback))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_message)
    )

    logger.info("STARTING BOT")
    application.run_polling()
