"""Moderation notification target resolution and Telegram delivery"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.bot.util.telegram_api import call_telegram_api
from app.bot.util.telegram_message import (
    build_send_message_kwargs,
    message_thread_id_from_message,
)
from app.config import Settings
from app.domain import ModerationAction


@dataclass(frozen=True)
class NotificationTarget:
    kind: str
    value: str


def resolve_notification_target(
    *,
    settings: Settings,
    runtime_target: str | None,
) -> NotificationTarget:
    parsed_runtime_target = parse_notification_target(runtime_target)
    if parsed_runtime_target is not None:
        return parsed_runtime_target

    if settings.admin_id is not None:
        return NotificationTarget(kind="user_id", value=str(settings.admin_id))

    if settings.admin_username:
        return NotificationTarget(
            kind="username",
            value=settings.admin_username.strip().lstrip("@"),
        )

    return NotificationTarget(kind="username", value="admin")


def parse_notification_target(raw_target: str | None) -> NotificationTarget | None:
    if raw_target is None:
        return None

    target = raw_target.strip()
    if not target:
        return None

    if target.lstrip("-").isdigit():
        return NotificationTarget(kind="user_id", value=str(int(target)))

    return NotificationTarget(kind="username", value=target.lstrip("@"))


def format_admin_target(target: NotificationTarget) -> str:
    if target.kind == "user_id":
        return f"admin_id:{target.value}"
    return f"@{target.value}"


async def send_admin_notification(
    *,
    bot: Any,
    target: NotificationTarget,
    group_chat_id: int,
    message: Any,
    text: str,
    chat_id: int,
    user_id: int,
    message_text: str,
    logger: logging.Logger,
) -> None:
    if target.kind == "user_id":
        send_kwargs = build_send_message_kwargs(
            chat_id=int(target.value),
            text=text,
        )
    else:
        send_kwargs = build_send_message_kwargs(
            chat_id=group_chat_id,
            text=text,
            message_thread_id=message_thread_id_from_message(message),
        )

    await call_telegram_api(
        operation=ModerationAction.NOTIFY_ADMIN.value,
        call=bot.send_message(**send_kwargs),
        chat_id=chat_id,
        user_id=user_id,
        message_text=message_text,
        logger=logger,
    )
