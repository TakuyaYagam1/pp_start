"""Telegram actions for stop-word warning moderation flow"""

from __future__ import annotations

import logging
from typing import Any

from app.bot.util.telegram_api import call_telegram_api
from app.bot.util.telegram_message import (
    build_send_message_kwargs,
    message_thread_id_from_message,
)
from app.domain import ModerationAction

STOP_WORD_WARNING_TERM_MAX_LENGTH = 80


def format_stop_word_warning_term(term: str | None) -> str:
    if term is None:
        return "запрещенный термин"

    normalized_term = " ".join(term.split())
    if not normalized_term:
        return "запрещенный термин"
    if len(normalized_term) <= STOP_WORD_WARNING_TERM_MAX_LENGTH:
        return normalized_term
    return f"{normalized_term[:STOP_WORD_WARNING_TERM_MAX_LENGTH]}..."


def build_stop_word_warning_text(term: str | None) -> str:
    warning_term = format_stop_word_warning_term(term)
    return (
        f"⚠️ Слово или фраза «{warning_term}» запрещены в чате. "
        "В следующий раз вы будете исключены из группы."
    )


async def send_stop_word_warning(
    *,
    bot: Any,
    message: Any,
    chat_id: int,
    user_id: int,
    message_text: str,
    matched_term: str | None,
    logger: logging.Logger,
) -> Any:
    send_kwargs = build_send_message_kwargs(
        chat_id=chat_id,
        text=build_stop_word_warning_text(matched_term),
        message_thread_id=message_thread_id_from_message(message),
    )

    return await call_telegram_api(
        operation=ModerationAction.WARN_USER.value,
        call=bot.send_message(**send_kwargs),
        chat_id=chat_id,
        user_id=user_id,
        message_text=message_text,
        logger=logger,
    )
