from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import TypeVar

from app.logging import get_logger, log_app_event


T = TypeVar("T")


async def call_telegram_api(
    *,
    operation: str,
    call: Awaitable[T],
    chat_id: int | None = None,
    user_id: int | None = None,
    message_text: str | None = None,
    logger: logging.Logger | None = None,
) -> T:
    try:
        return await call
    except Exception as exc:
        log_telegram_api_error(
            operation=operation,
            error=exc,
            chat_id=chat_id,
            user_id=user_id,
            message_text=message_text,
            logger=logger,
        )
        raise


def log_telegram_api_error(
    *,
    operation: str,
    error: Exception,
    chat_id: int | None = None,
    user_id: int | None = None,
    message_text: str | None = None,
    logger: logging.Logger | None = None,
) -> None:
    log_app_event(
        logger or get_logger("app"),
        event="telegram_api_error",
        chat_id=chat_id,
        user_id=user_id,
        message_text=message_text,
        action=operation,
        details=f"error_type={type(error).__name__}",
        level=logging.ERROR,
    )
