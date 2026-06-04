"""Telegram message kwargs helpers for topic-aware send calls"""

from __future__ import annotations

from typing import Any


def message_thread_id_from_message(message: Any) -> int | None:
    message_thread_id = getattr(message, "message_thread_id", None)
    if message_thread_id is None:
        return None
    return int(message_thread_id)


def build_send_message_kwargs(
    *,
    chat_id: int,
    text: str,
    message_thread_id: int | None = None,
    reply_markup: Any | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
    }
    if message_thread_id is not None:
        kwargs["message_thread_id"] = int(message_thread_id)
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    return kwargs
