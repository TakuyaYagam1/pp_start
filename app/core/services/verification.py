from __future__ import annotations

import asyncio
import logging
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any

from aiogram.types import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.cache.redis import (
    BlacklistRepository,
    PendingVerificationRepository,
    VerifiedUserRepository,
)
from app.observability.logging import get_logger, log_app_event
from app.tg_bot.utils.telegram_api import call_telegram_api


VERIFY_CALLBACK_PREFIX = "verify_user"
VERIFY_BUTTON_TEXT = "✅ Я человек"
VERIFY_SUCCESS_CALLBACK_ANSWER = "✅ Готово, доступ открыт"
VERIFY_SUCCESS_PRIVATE_MESSAGE = "✅ Готово, доступ открыт. Добро пожаловать в чат"
VERIFY_WRONG_USER_CALLBACK_ANSWER = "❌ Эта кнопка не для вас"
VERIFY_EXPIRED_CALLBACK_ANSWER = "❌ Проверка уже недействительна"
COUNTDOWN_INTERVAL_SECONDS = 1
UNVERIFIED_MEMBER_PERMISSIONS = ChatPermissions(can_send_messages=False)
VERIFIED_MEMBER_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_react_to_messages=True,
)


@dataclass(frozen=True)
class VerificationMessage:
    text: str
    reply_markup: InlineKeyboardMarkup


@dataclass(frozen=True)
class VerifyCallbackPayload:
    user_id: int
    chat_id: int | None = None


def build_verify_callback_data(user_id: int, *, chat_id: int | None = None) -> str:
    if chat_id is None:
        return f"{VERIFY_CALLBACK_PREFIX}:{user_id}"
    return f"{VERIFY_CALLBACK_PREFIX}:{chat_id}:{user_id}"


def parse_verify_callback_payload(
    callback_data: str | None,
) -> VerifyCallbackPayload | None:
    if callback_data is None:
        return None

    parts = callback_data.split(":")
    if len(parts) == 2 and parts[0] == VERIFY_CALLBACK_PREFIX:
        try:
            return VerifyCallbackPayload(user_id=int(parts[1]))
        except ValueError:
            return None

    if len(parts) == 3 and parts[0] == VERIFY_CALLBACK_PREFIX:
        try:
            return VerifyCallbackPayload(chat_id=int(parts[1]), user_id=int(parts[2]))
        except ValueError:
            return None

    return None


def parse_verify_callback_data(callback_data: str | None) -> int | None:
    payload = parse_verify_callback_payload(callback_data)
    if payload is None:
        return None
    return payload.user_id


def format_minutes(minutes: int) -> str:
    normalized_minutes = max(1, minutes)
    if normalized_minutes % 10 == 1 and normalized_minutes % 100 != 11:
        return f"{normalized_minutes} минуту"
    if 2 <= normalized_minutes % 10 <= 4 and not 12 <= normalized_minutes % 100 <= 14:
        return f"{normalized_minutes} минуты"
    return f"{normalized_minutes} минут"


def format_countdown(seconds: float) -> str:
    normalized_seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(normalized_seconds, 60)
    return f"{minutes}:{remaining_seconds:02d}"


def build_verification_message(
    *,
    user_id: int,
    user_full_name: str | None = None,
    timeout_seconds: int = 180,
    remaining_seconds: int | None = None,
    chat_id: int | None = None,
) -> VerificationMessage:
    timeout_text = format_minutes(timeout_seconds // 60)
    countdown_text = format_countdown(
        timeout_seconds if remaining_seconds is None else remaining_seconds
    )
    greeting = f"{user_full_name}, " if user_full_name else ""
    if chat_id is None:
        text = (
            f"⚠️ {greeting}подтвердите, что вы человек. "
            f"Нажмите кнопку «{VERIFY_BUTTON_TEXT}». "
            f"У вас {timeout_text}, иначе вы будете удалены из чата.\n\n"
            f"⏳ Осталось: {countdown_text}"
        )
    else:
        text = (
            f"⚠️ {greeting}подтвердите, что вы человек. "
            f"Нажмите кнопку «{VERIFY_BUTTON_TEXT}» в течение {timeout_text}. "
            "До подтверждения вы не можете читать и писать в группе. "
            "После проверки бот откроет доступ к чату.\n\n"
            f"⏳ Осталось: {countdown_text}"
        )
    reply_markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=VERIFY_BUTTON_TEXT,
                    callback_data=build_verify_callback_data(
                        user_id,
                        chat_id=chat_id,
                    ),
                )
            ]
        ]
    )
    return VerificationMessage(text=text, reply_markup=reply_markup)


def build_verification_timeout_message(*, timeout_seconds: float) -> str:
    timeout_text = format_minutes(int(timeout_seconds) // 60)
    return (
        f"❌ Проверка не пройдена за {timeout_text}. "
        "Заявка отклонена, доступ к группе заблокирован."
    )


async def send_verification_message(
    bot: Any,
    *,
    chat_id: int,
    user_id: int,
    user_full_name: str | None = None,
    message_thread_id: int | None = None,
    timeout_seconds: int = 180,
) -> Message:
    verification_message = build_verification_message(
        user_id=user_id,
        user_full_name=user_full_name,
        timeout_seconds=timeout_seconds,
    )
    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "text": verification_message.text,
        "reply_markup": verification_message.reply_markup,
    }
    if message_thread_id is not None:
        kwargs["message_thread_id"] = message_thread_id

    return await call_telegram_api(
        operation="send_verification_message",
        call=bot.send_message(**kwargs),
        chat_id=chat_id,
        user_id=user_id,
    )


async def send_join_request_verification_message(
    bot: Any,
    *,
    chat_id: int,
    user_chat_id: int,
    user_id: int,
    user_full_name: str | None = None,
    timeout_seconds: int = 180,
) -> Message:
    verification_message = build_verification_message(
        user_id=user_id,
        user_full_name=user_full_name,
        timeout_seconds=timeout_seconds,
        chat_id=chat_id,
    )
    return await call_telegram_api(
        operation="send_join_request_verification_message",
        call=bot.send_message(
            chat_id=user_chat_id,
            text=verification_message.text,
            reply_markup=verification_message.reply_markup,
        ),
        chat_id=chat_id,
        user_id=user_id,
    )


def _cancel_verification_task(
    *,
    task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]] | None,
    chat_id: int,
    user_id: int,
) -> None:
    if task_registry is None:
        return

    task = task_registry.pop((chat_id, user_id), None)
    if task is not None and not task.done():
        task.cancel()


async def _call_telegram_api_best_effort(
    *,
    operation: str,
    call: Any,
    chat_id: int,
    user_id: int,
    logger: logging.Logger,
) -> None:
    try:
        await call_telegram_api(
            operation=operation,
            call=call,
            chat_id=chat_id,
            user_id=user_id,
            logger=logger,
        )
    except Exception:
        return


async def _complete_pending_verification(
    *,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    verified_user_repository: VerifiedUserRepository,
    chat_id: int,
    user_id: int,
    approve_join_request: bool = False,
    task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]] | None = None,
    countdown_task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]]
    | None = None,
) -> bool:
    pending = await pending_verification_repository.get(
        chat_id=chat_id, user_id=user_id
    )
    if pending is None:
        return False

    if approve_join_request:
        await call_telegram_api(
            operation="approve_chat_join_request",
            call=bot.approve_chat_join_request(
                chat_id=pending.chat_id,
                user_id=pending.user_id,
            ),
            chat_id=pending.chat_id,
            user_id=pending.user_id,
        )
    else:
        await _call_telegram_api_best_effort(
            operation="restore_verified_member_permissions",
            call=bot.restrict_chat_member(
                chat_id=pending.chat_id,
                user_id=pending.user_id,
                permissions=VERIFIED_MEMBER_PERMISSIONS,
            ),
            chat_id=pending.chat_id,
            user_id=pending.user_id,
            logger=get_logger("app"),
        )

    await verified_user_repository.mark_verified(
        chat_id=pending.chat_id,
        user_id=pending.user_id,
    )
    await pending_verification_repository.delete(
        chat_id=pending.chat_id,
        user_id=pending.user_id,
    )
    _cancel_verification_task(
        task_registry=task_registry,
        chat_id=pending.chat_id,
        user_id=pending.user_id,
    )
    _cancel_verification_task(
        task_registry=countdown_task_registry,
        chat_id=pending.chat_id,
        user_id=pending.user_id,
    )
    await _call_telegram_api_best_effort(
        operation="delete_verification_message",
        call=bot.delete_message(
            chat_id=pending.verification_chat_id or pending.chat_id,
            message_id=pending.verification_message_id,
        ),
        chat_id=pending.chat_id,
        user_id=pending.user_id,
        logger=get_logger("app"),
    )
    if pending.verification_chat_id is not None:
        await _call_telegram_api_best_effort(
            operation="send_verification_success_message",
            call=bot.send_message(
                chat_id=pending.verification_chat_id,
                text=VERIFY_SUCCESS_PRIVATE_MESSAGE,
            ),
            chat_id=pending.chat_id,
            user_id=pending.user_id,
            logger=get_logger("app"),
        )
    return True


async def complete_verification_from_callback(
    *,
    callback_query: Any,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    verified_user_repository: VerifiedUserRepository,
    task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]] | None = None,
    countdown_task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]]
    | None = None,
) -> bool:
    payload = parse_verify_callback_payload(getattr(callback_query, "data", None))
    from_user = getattr(callback_query, "from_user", None)
    callback_user_id = getattr(from_user, "id", None)

    if payload is None or callback_user_id is None:
        await callback_query.answer(
            text=VERIFY_EXPIRED_CALLBACK_ANSWER, show_alert=True
        )
        return False

    if payload.user_id != callback_user_id:
        await callback_query.answer(
            text=VERIFY_WRONG_USER_CALLBACK_ANSWER,
            show_alert=True,
        )
        return False

    chat_id = payload.chat_id
    if chat_id is None:
        message = getattr(callback_query, "message", None)
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)

    if chat_id is None:
        await callback_query.answer(
            text=VERIFY_EXPIRED_CALLBACK_ANSWER, show_alert=True
        )
        return False

    pending = await pending_verification_repository.get(
        chat_id=int(chat_id),
        user_id=payload.user_id,
    )
    if pending is None:
        await callback_query.answer(
            text=VERIFY_EXPIRED_CALLBACK_ANSWER, show_alert=True
        )
        return False

    completed = await _complete_pending_verification(
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        verified_user_repository=verified_user_repository,
        chat_id=pending.chat_id,
        user_id=pending.user_id,
        approve_join_request=payload.chat_id is not None,
        task_registry=task_registry,
        countdown_task_registry=countdown_task_registry,
    )
    if not completed:
        await callback_query.answer(
            text=VERIFY_EXPIRED_CALLBACK_ANSWER, show_alert=True
        )
        return False

    await callback_query.answer(text=VERIFY_SUCCESS_CALLBACK_ANSWER)
    return True


async def remove_unverified_user_after_timeout(
    *,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    chat_id: int,
    user_id: int,
    timeout_seconds: float,
    countdown_task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]]
    | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    await asyncio.sleep(timeout_seconds)

    pending = await pending_verification_repository.get(
        chat_id=chat_id, user_id=user_id
    )
    if pending is None:
        return False

    event_logger = logger or get_logger("app")
    await call_telegram_api(
        operation="verification_timeout_ban",
        call=bot.ban_chat_member(chat_id=chat_id, user_id=user_id),
        chat_id=chat_id,
        user_id=user_id,
        logger=event_logger,
    )
    await call_telegram_api(
        operation="verification_timeout_unban",
        call=bot.unban_chat_member(chat_id=chat_id, user_id=user_id),
        chat_id=chat_id,
        user_id=user_id,
        logger=event_logger,
    )
    await pending_verification_repository.delete(chat_id=chat_id, user_id=user_id)
    _cancel_verification_task(
        task_registry=countdown_task_registry,
        chat_id=chat_id,
        user_id=user_id,
    )
    await _call_telegram_api_best_effort(
        operation="delete_verification_message",
        call=bot.delete_message(
            chat_id=pending.verification_chat_id or pending.chat_id,
            message_id=pending.verification_message_id,
        ),
        chat_id=chat_id,
        user_id=user_id,
        logger=event_logger,
    )

    log_app_event(
        event_logger,
        event="verification_timeout_removed",
        chat_id=chat_id,
        user_id=user_id,
        action="ban_unban",
        details="unverified user removed after verification timeout",
    )
    return True


async def restrict_unverified_member(
    *,
    bot: Any,
    chat_id: int,
    user_id: int,
    logger: logging.Logger,
) -> None:
    await _call_telegram_api_best_effort(
        operation="restrict_unverified_member",
        call=bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=UNVERIFIED_MEMBER_PERMISSIONS,
        ),
        chat_id=chat_id,
        user_id=user_id,
        logger=logger,
    )


async def block_unverified_join_request_after_timeout(
    *,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    chat_id: int,
    user_id: int,
    timeout_seconds: float,
    countdown_task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]]
    | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    await asyncio.sleep(timeout_seconds)

    pending = await pending_verification_repository.get(
        chat_id=chat_id, user_id=user_id
    )
    if pending is None:
        return False

    event_logger = logger or get_logger("app")
    if pending.verification_chat_id is not None:
        await _call_telegram_api_best_effort(
            operation="send_verification_timeout_message",
            call=bot.send_message(
                chat_id=pending.verification_chat_id,
                text=build_verification_timeout_message(
                    timeout_seconds=timeout_seconds
                ),
            ),
            chat_id=chat_id,
            user_id=user_id,
            logger=event_logger,
        )
    await _call_telegram_api_best_effort(
        operation="verification_timeout_decline_join_request",
        call=bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id),
        chat_id=chat_id,
        user_id=user_id,
        logger=event_logger,
    )
    await _call_telegram_api_best_effort(
        operation="verification_timeout_ban_join_request",
        call=bot.ban_chat_member(chat_id=chat_id, user_id=user_id),
        chat_id=chat_id,
        user_id=user_id,
        logger=event_logger,
    )
    await pending_verification_repository.delete(chat_id=chat_id, user_id=user_id)
    _cancel_verification_task(
        task_registry=countdown_task_registry,
        chat_id=chat_id,
        user_id=user_id,
    )

    log_app_event(
        event_logger,
        event="verification_timeout_blocked",
        chat_id=chat_id,
        user_id=user_id,
        action="decline_and_ban",
        details="join request user blocked after verification timeout",
    )
    return True


def schedule_unverified_user_removal(
    *,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    chat_id: int,
    user_id: int,
    timeout_seconds: float,
    task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]] | None = None,
    countdown_task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]]
    | None = None,
    logger: logging.Logger | None = None,
) -> asyncio.Task[bool]:
    task = asyncio.create_task(
        remove_unverified_user_after_timeout(
            bot=bot,
            pending_verification_repository=pending_verification_repository,
            chat_id=chat_id,
            user_id=user_id,
            timeout_seconds=timeout_seconds,
            countdown_task_registry=countdown_task_registry,
            logger=logger,
        )
    )
    _register_verification_task(
        task=task,
        task_registry=task_registry,
        chat_id=chat_id,
        user_id=user_id,
    )
    return task


def schedule_join_request_timeout(
    *,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    chat_id: int,
    user_id: int,
    timeout_seconds: float,
    task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]] | None = None,
    countdown_task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]]
    | None = None,
    logger: logging.Logger | None = None,
) -> asyncio.Task[bool]:
    task = asyncio.create_task(
        block_unverified_join_request_after_timeout(
            bot=bot,
            pending_verification_repository=pending_verification_repository,
            blacklist_repository=blacklist_repository,
            chat_id=chat_id,
            user_id=user_id,
            timeout_seconds=timeout_seconds,
            countdown_task_registry=countdown_task_registry,
            logger=logger,
        )
    )
    _register_verification_task(
        task=task,
        task_registry=task_registry,
        chat_id=chat_id,
        user_id=user_id,
    )
    return task


async def update_verification_countdown(
    *,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    chat_id: int,
    user_id: int,
    user_full_name: str | None,
    timeout_seconds: int,
    join_request: bool = False,
    logger: logging.Logger | None = None,
) -> bool:
    event_logger = logger or get_logger("app")
    for remaining_seconds in range(timeout_seconds - 1, 0, -1):
        await asyncio.sleep(COUNTDOWN_INTERVAL_SECONDS)
        pending = await pending_verification_repository.get(
            chat_id=chat_id,
            user_id=user_id,
        )
        if pending is None:
            return False

        verification_message = build_verification_message(
            user_id=user_id,
            user_full_name=user_full_name,
            timeout_seconds=timeout_seconds,
            remaining_seconds=remaining_seconds,
            chat_id=chat_id if join_request else None,
        )
        await _call_telegram_api_best_effort(
            operation="edit_verification_countdown",
            call=bot.edit_message_text(
                chat_id=pending.verification_chat_id or pending.chat_id,
                message_id=pending.verification_message_id,
                text=verification_message.text,
                reply_markup=verification_message.reply_markup,
            ),
            chat_id=chat_id,
            user_id=user_id,
            logger=event_logger,
        )
    return True


def schedule_verification_countdown(
    *,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    chat_id: int,
    user_id: int,
    user_full_name: str | None,
    timeout_seconds: int,
    join_request: bool = False,
    task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]] | None = None,
    logger: logging.Logger | None = None,
) -> asyncio.Task[bool]:
    task = asyncio.create_task(
        update_verification_countdown(
            bot=bot,
            pending_verification_repository=pending_verification_repository,
            chat_id=chat_id,
            user_id=user_id,
            user_full_name=user_full_name,
            timeout_seconds=timeout_seconds,
            join_request=join_request,
            logger=logger,
        )
    )
    _register_verification_task(
        task=task,
        task_registry=task_registry,
        chat_id=chat_id,
        user_id=user_id,
    )
    return task


def _register_verification_task(
    *,
    task: asyncio.Task[bool],
    task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]] | None,
    chat_id: int,
    user_id: int,
) -> None:
    if task_registry is None:
        return

    key = (chat_id, user_id)
    previous_task = task_registry.get(key)
    if previous_task is not None and not previous_task.done():
        previous_task.cancel()
    task_registry[key] = task

    def remove_completed_task(completed_task: asyncio.Task[bool]) -> None:
        if task_registry.get(key) is completed_task:
            task_registry.pop(key, None)

    task.add_done_callback(remove_completed_task)


async def start_join_request_verification(
    *,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    chat_id: int,
    user_id: int,
    user_chat_id: int,
    user_full_name: str | None,
    timeout_seconds: int,
    task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]] | None = None,
    countdown_task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]]
    | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    event_logger = logger or get_logger("app")
    pending = await pending_verification_repository.get(
        chat_id=chat_id, user_id=user_id
    )
    if pending is not None:
        return False

    sent_message = await send_join_request_verification_message(
        bot,
        chat_id=chat_id,
        user_chat_id=user_chat_id,
        user_id=user_id,
        user_full_name=user_full_name,
        timeout_seconds=timeout_seconds,
    )
    await pending_verification_repository.create(
        user_id=user_id,
        chat_id=chat_id,
        verification_message_id=int(sent_message.message_id),
        verification_chat_id=user_chat_id,
    )
    schedule_join_request_timeout(
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        blacklist_repository=blacklist_repository,
        chat_id=chat_id,
        user_id=user_id,
        timeout_seconds=timeout_seconds,
        task_registry=task_registry,
        countdown_task_registry=countdown_task_registry,
        logger=event_logger,
    )
    if countdown_task_registry is not None:
        schedule_verification_countdown(
            bot=bot,
            pending_verification_repository=pending_verification_repository,
            chat_id=chat_id,
            user_id=user_id,
            user_full_name=user_full_name,
            timeout_seconds=timeout_seconds,
            join_request=True,
            task_registry=countdown_task_registry,
            logger=event_logger,
        )
    log_app_event(
        event_logger,
        event="join_request_verification_started",
        chat_id=chat_id,
        user_id=user_id,
        action="send_private_challenge",
        details=f"user_chat_id={user_chat_id}",
    )
    return True


async def start_member_verification(
    *,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    chat_id: int,
    user_id: int,
    user_full_name: str | None,
    timeout_seconds: int,
    message_thread_id: int | None = None,
    task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]] | None = None,
    countdown_task_registry: MutableMapping[tuple[int, int], asyncio.Task[bool]]
    | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    event_logger = logger or get_logger("app")
    pending = await pending_verification_repository.get(
        chat_id=chat_id, user_id=user_id
    )
    if pending is not None:
        return False

    await restrict_unverified_member(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        logger=event_logger,
    )
    sent_message = await send_verification_message(
        bot,
        chat_id=chat_id,
        user_id=user_id,
        user_full_name=user_full_name,
        message_thread_id=message_thread_id,
        timeout_seconds=timeout_seconds,
    )
    await pending_verification_repository.create(
        user_id=user_id,
        chat_id=chat_id,
        verification_message_id=int(sent_message.message_id),
        message_thread_id=message_thread_id,
    )
    schedule_unverified_user_removal(
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        chat_id=chat_id,
        user_id=user_id,
        timeout_seconds=timeout_seconds,
        task_registry=task_registry,
        countdown_task_registry=countdown_task_registry,
        logger=event_logger,
    )
    if countdown_task_registry is not None:
        schedule_verification_countdown(
            bot=bot,
            pending_verification_repository=pending_verification_repository,
            chat_id=chat_id,
            user_id=user_id,
            user_full_name=user_full_name,
            timeout_seconds=timeout_seconds,
            task_registry=countdown_task_registry,
            logger=event_logger,
        )
    log_app_event(
        event_logger,
        event="member_verification_started",
        chat_id=chat_id,
        user_id=user_id,
        action="restrict_and_send_group_challenge",
        details="normal member join fallback",
    )
    return True
