"""Moderation message context and formatting helpers"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.observability.logging import get_logger

SPAM_NOTIFICATION_MAX_LENGTH = 3900
SPAM_NOTIFICATION_TRUNCATED_SUFFIX = "\n[message truncated]"


@dataclass(frozen=True)
class ModerationMessageContext:
    chat_id: int
    user_id: int
    message_id: int
    message_text: str
    logger: logging.Logger


def build_moderation_message_context(
    message: Any,
    *,
    logger: logging.Logger | None = None,
) -> ModerationMessageContext:
    return ModerationMessageContext(
        chat_id=int(message.chat.id),
        user_id=int(message.from_user.id),
        message_id=int(message.message_id),
        message_text=message_text_for_log(message),
        logger=logger or get_logger("app"),
    )


def truncate_message_text(text: str, *, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    if max_length <= len(SPAM_NOTIFICATION_TRUNCATED_SUFFIX):
        return SPAM_NOTIFICATION_TRUNCATED_SUFFIX[:max_length]
    return (
        text[: max_length - len(SPAM_NOTIFICATION_TRUNCATED_SUFFIX)]
        + SPAM_NOTIFICATION_TRUNCATED_SUFFIX
    )


def build_spam_notification_text(
    *,
    admin_target_text: str,
    spammer: str,
    user_id: int,
    reason: str,
    message_reference: str,
    message_text: str,
) -> str:
    prefix = (
        f"{admin_target_text}, обнаружен спам.\n"
        f"Пользователь: {spammer}\n"
        f"user_id: {user_id}\n"
        f"Причина: {reason}\n"
        f"Сообщение: {message_reference}\n"
        "Текст: "
    )
    message_text_budget = max(0, SPAM_NOTIFICATION_MAX_LENGTH - len(prefix))
    return prefix + truncate_message_text(message_text, max_length=message_text_budget)


def format_spammer(from_user: Any) -> str:
    username = getattr(from_user, "username", None)
    user_id = int(from_user.id)
    if username:
        username = str(username).strip()
        formatted_username = username if username.startswith("@") else f"@{username}"
        return f"{formatted_username} ({user_id})"
    return str(user_id)


def build_message_reference(message: Any) -> str:
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    message_id = int(getattr(message, "message_id"))
    username = getattr(chat, "username", None)

    if username:
        public_username = str(username).strip().lstrip("@")
        if public_username:
            return f"https://t.me/{public_username}/{message_id}"

    return f"chat_id={chat_id}; message_id={message_id}"


def message_text_for_log(message: Any) -> str:
    text = getattr(message, "text", None)
    if text:
        return str(text)

    caption = getattr(message, "caption", None)
    if caption:
        return str(caption)

    for field in (
        "sticker",
        "animation",
        "video",
        "document",
        "audio",
        "voice",
        "video_note",
    ):
        value = getattr(message, field, None)
        file_unique_id = getattr(value, "file_unique_id", None)
        if file_unique_id:
            file_name = getattr(value, "file_name", None)
            if file_name:
                return f"[{field}:{file_unique_id}:{file_name}]"
            return f"[{field}:{file_unique_id}]"

    photos = getattr(message, "photo", None)
    if photos:
        file_unique_id = getattr(photos[-1], "file_unique_id", None)
        if file_unique_id:
            return f"[photo:{file_unique_id}]"

    return "[non_text_message]"
