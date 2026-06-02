from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from app.config import Settings


APP_LOGGER_NAME = "app"
LOG_FIELD_DEFAULT = "-"


class RedactingStructuredFormatter(logging.Formatter):
    def __init__(
        self,
        *,
        secrets: Iterable[str],
        fmt: str,
        datefmt: str | None = None,
    ) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: logging.LogRecord) -> str:
        for field in (
            "event",
            "chat_id",
            "user_id",
            "message_text",
            "action",
            "details",
        ):
            if not hasattr(record, field):
                setattr(record, field, LOG_FIELD_DEFAULT)

        formatted = super().format(record)
        for secret in self._secrets:
            formatted = formatted.replace(secret, "***REDACTED***")
        return formatted


def _log_level(level: str) -> int:
    resolved_level = getattr(logging, level.upper(), None)
    if not isinstance(resolved_level, int):
        return logging.INFO
    return resolved_level


def configure_logging(settings: Settings) -> logging.Logger:
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.setLevel(_log_level(settings.log_level))
    close_logger_handlers(logger)
    logger.propagate = False

    log_file = Path(settings.log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = RedactingStructuredFormatter(
        secrets=(
            settings.bot_token.get_secret_value(),
            settings.llm_api_key.get_secret_value(),
        ),
        fmt=(
            "%(asctime)s level=%(levelname)s event=%(event)s "
            "chat_id=%(chat_id)s user_id=%(user_id)s "
            "message_text=%(message_text)s action=%(action)s details=%(details)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def close_logger_handlers(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_app_event(
    logger: logging.Logger,
    *,
    event: str,
    chat_id: int | None = None,
    user_id: int | None = None,
    message_text: str | None = None,
    action: str | None = None,
    details: str | None = None,
    level: int = logging.INFO,
) -> None:
    logger.log(
        level,
        event,
        extra={
            "event": event,
            "chat_id": chat_id if chat_id is not None else LOG_FIELD_DEFAULT,
            "user_id": user_id if user_id is not None else LOG_FIELD_DEFAULT,
            "message_text": message_text or LOG_FIELD_DEFAULT,
            "action": action or LOG_FIELD_DEFAULT,
            "details": details or LOG_FIELD_DEFAULT,
        },
    )


def log_spam_event(
    logger: logging.Logger,
    *,
    chat_id: int,
    user_id: int,
    message_text: str,
    action: str,
    details: str,
) -> None:
    log_app_event(
        logger,
        event="spam_detected",
        chat_id=chat_id,
        user_id=user_id,
        message_text=message_text,
        action=action,
        details=details,
    )
