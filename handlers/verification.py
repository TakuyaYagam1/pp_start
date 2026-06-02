import asyncio

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from db.database import add_pending, get_pending, remove_pending
from utils.logger import logger

router = Router()

pending_tasks: dict[tuple[int, int], asyncio.Task] = {}


async def kick_if_not_verified(
    bot: Bot, user_id: int, chat_id: int, delay: int = 180
) -> None:
    await asyncio.sleep(delay)
    row = await get_pending(user_id, chat_id)
    if row is None:
        return
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id)
        await remove_pending(user_id, chat_id)
        logger.info("Kicked unverified user %s from chat %s", user_id, chat_id)
    except Exception as exc:
        logger.error("Failed to kick user %s from chat %s: %s", user_id, chat_id, exc)
    finally:
        pending_tasks.pop((user_id, chat_id), None)


@router.chat_member()
async def on_new_member(event: ChatMemberUpdated, bot: Bot) -> None:
    old = event.old_chat_member
    new = event.new_chat_member

    if new.status != "member":
        return
    if old.status in ("member", "administrator", "creator"):
        return

    user_id = new.user.id
    chat_id = event.chat.id
    thread_id: int | None = getattr(event.chat, "message_thread_id", None)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Я человек ✅", callback_data=f"verify_{user_id}"
                )
            ]
        ]
    )

    sent = await bot.send_message(
        chat_id=chat_id,
        text=(
            f"Привет, {new.user.full_name}! "
            "Нажми кнопку ниже в течение 3 минут, чтобы подтвердить, что ты человек."
        ),
        message_thread_id=thread_id,
        reply_markup=keyboard,
    )

    await add_pending(user_id, chat_id, thread_id, sent.message_id)

    task = asyncio.create_task(kick_if_not_verified(bot, user_id, chat_id))
    pending_tasks[(user_id, chat_id)] = task
    logger.info("New member %s in chat %s — verification started", user_id, chat_id)


@router.callback_query(F.data.startswith("verify_"))
async def on_verify_button(query: CallbackQuery, bot: Bot) -> None:
    expected_user_id = int(query.data.split("_", 1)[1])

    if query.from_user.id != expected_user_id:
        await query.answer("Это не для вас", show_alert=True)
        return

    user_id = query.from_user.id
    chat_id = query.message.chat.id

    task = pending_tasks.pop((user_id, chat_id), None)
    if task:
        task.cancel()

    await remove_pending(user_id, chat_id)

    try:
        await bot.delete_message(chat_id, query.message.message_id)
    except Exception:
        pass

    await query.answer("Спасибо, вы верифицированы ✅")
    logger.info("User %s verified via button in chat %s", user_id, chat_id)


@router.message(Command("verify"))
async def on_verify_command(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id

    row = await get_pending(user_id, chat_id)
    if row is None:
        await message.reply("Вы уже верифицированы или не ожидаете верификации")
        return

    task = pending_tasks.pop((user_id, chat_id), None)
    if task:
        task.cancel()

    await remove_pending(user_id, chat_id)

    if row["verification_message_id"]:
        try:
            await bot.delete_message(chat_id, row["verification_message_id"])
        except Exception:
            pass

    await message.reply("Спасибо, вы верифицированы ✅")
    logger.info("User %s verified via /verify in chat %s", user_id, chat_id)
