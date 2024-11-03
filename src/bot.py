import logging
import os
from functools import partial

import telegram
from src.pipe import Pipeline
from tabulate import tabulate
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


logger = logging.getLogger("logger")


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
        table = tabulate(
            [
                {
                    k: v
                    for k, v in row.items()
                    if k in ["Category", "Percent", "Bank"]
                }
                for row in rows
            ],
            headers="keys",
            tablefmt="simple",
        )
        await update.message.reply_text(ok_message)
        await update.message.reply_text(
            f"```\n{table}\n```",
            parse_mode=telegram.constants.ParseMode.MARKDOWN_V2,
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

    logger.info("STARTING BOT")
    application.run_polling()
