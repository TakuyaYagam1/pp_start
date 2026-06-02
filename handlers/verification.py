import asyncio

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command
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

# Each entry: {'kick': Task, 'timer': Task}
pending_tasks: dict[tuple[int, int], dict[str, asyncio.Task]] = {}


class IsUnverified(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if message.from_user is None:
            return False
        row = await get_pending(message.from_user.id, message.chat.id)
        return row is not None


async def update_timer_message(
    bot: Bot,
    user_id: int,
    chat_id: int,
    msg_id: int,
    keyboard: InlineKeyboardMarkup,
    total: int = 180,
    interval: int = 30,
) -> None:
    remaining = total
    while True:
        await asyncio.sleep(interval)
        remaining -= interval
        if remaining <= 0:
            return
        minutes = remaining // 60
        seconds = remaining % 60
        text = f"Подтвердите, что вы человек. Осталось: {minutes}:{seconds:02d}"
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=keyboard,
            )
        except TelegramBadRequest:
            pass
        except Exception as exc:
            logger.debug("Timer update error for %s in %s: %s", user_id, chat_id, exc)


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


def _cancel_user_tasks(user_id: int, chat_id: int) -> None:
    tasks = pending_tasks.pop((user_id, chat_id), {})
    for task in tasks.values():
        task.cancel()


# T13: registered first — intercepts messages from unverified users before any other handler.
# IsUnverified filter makes this handler match only for users still in pending_verifications,
# so verified users' messages fall through to subsequent handlers (spam_flow, etc.).
# ~Command("verify") excludes /verify so it reaches on_verify_command.
@router.message(IsUnverified(), ~Command("verify"))
async def on_unverified_message(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id
    try:
        await bot.delete_message(chat_id, message.message_id)
    except TelegramBadRequest:
        pass
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="Сначала пройдите верификацию — нажмите кнопку в приветственном сообщении.",
            message_thread_id=message.message_thread_id,
        )
    except TelegramBadRequest:
        pass
    logger.info("Deleted message from unverified user %s in chat %s", user_id, chat_id)


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

    kick_task = asyncio.create_task(kick_if_not_verified(bot, user_id, chat_id))
    timer_task = asyncio.create_task(
        update_timer_message(bot, user_id, chat_id, sent.message_id, keyboard)
    )
    pending_tasks[(user_id, chat_id)] = {"kick": kick_task, "timer": timer_task}
    logger.info("New member %s in chat %s — verification started", user_id, chat_id)


@router.callback_query(F.data.startswith("verify_"))
async def on_verify_button(query: CallbackQuery, bot: Bot) -> None:
    expected_user_id = int(query.data.split("_", 1)[1])

    if query.from_user.id != expected_user_id:
        await query.answer("Это не для вас", show_alert=True)
        return

    user_id = query.from_user.id
    chat_id = query.message.chat.id

    _cancel_user_tasks(user_id, chat_id)

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

    _cancel_user_tasks(user_id, chat_id)

    await remove_pending(user_id, chat_id)

    if row["verification_message_id"]:
        try:
            await bot.delete_message(chat_id, row["verification_message_id"])
        except Exception:
            pass

    await message.reply("Спасибо, вы верифицированы ✅")
    logger.info("User %s verified via /verify in chat %s", user_id, chat_id)
