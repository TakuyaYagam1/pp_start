"""Moderation action facade for delete, notify, warn and kick flows"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.domain import SpamDetectionResult
from app.usecase.contract import AutoDeleteMessageStore
from app.usecase.moderation.auto_delete import AutoDeleteTaskRegistry
from app.usecase.moderation.flood_action import (
    delete_duplicate_flood_during_grace,
    kick_duplicate_flood,
    warn_duplicate_flood,
)
from app.usecase.moderation.spam_action import (
    delete_spam_message,
    notify_admin_about_spam,
)
from app.usecase.moderation.warning_action import warn_stop_word_spam


class ModerationService:
    async def notify_admin_about_spam(
        self,
        *,
        bot: Any,
        message: Any,
        spam_result: SpamDetectionResult,
        settings: Settings,
        notification_target: str | None = None,
        logger: logging.Logger | None = None,
    ) -> SpamDetectionResult:
        return await notify_admin_about_spam(
            bot=bot,
            message=message,
            spam_result=spam_result,
            settings=settings,
            notification_target=notification_target,
            logger=logger,
        )

    async def delete_spam_message(
        self,
        *,
        bot: Any,
        message: Any,
        spam_result: SpamDetectionResult,
        logger: logging.Logger | None = None,
    ) -> SpamDetectionResult:
        return await delete_spam_message(
            bot=bot,
            message=message,
            spam_result=spam_result,
            logger=logger,
        )

    async def warn_stop_word_spam(
        self,
        *,
        bot: Any,
        message: Any,
        spam_result: SpamDetectionResult,
        warning_message_ttl_seconds: float | None = None,
        auto_delete_message_repository: AutoDeleteMessageStore | None = None,
        auto_delete_task_registry: AutoDeleteTaskRegistry | None = None,
        logger: logging.Logger | None = None,
    ) -> SpamDetectionResult:
        return await warn_stop_word_spam(
            bot=bot,
            message=message,
            spam_result=spam_result,
            warning_message_ttl_seconds=warning_message_ttl_seconds,
            auto_delete_message_repository=auto_delete_message_repository,
            auto_delete_task_registry=auto_delete_task_registry,
            logger=logger,
        )

    async def warn_duplicate_flood(
        self,
        *,
        bot: Any,
        message: Any,
        spam_result: SpamDetectionResult,
        duplicate_message_ids: tuple[int, ...],
        warning_message_ttl_seconds: float | None = None,
        auto_delete_message_repository: AutoDeleteMessageStore | None = None,
        auto_delete_task_registry: AutoDeleteTaskRegistry | None = None,
        logger: logging.Logger | None = None,
    ) -> SpamDetectionResult:
        return await warn_duplicate_flood(
            bot=bot,
            message=message,
            spam_result=spam_result,
            duplicate_message_ids=duplicate_message_ids,
            warning_message_ttl_seconds=warning_message_ttl_seconds,
            auto_delete_message_repository=auto_delete_message_repository,
            auto_delete_task_registry=auto_delete_task_registry,
            logger=logger,
        )

    async def delete_duplicate_flood_during_grace(
        self,
        *,
        bot: Any,
        message: Any,
        spam_result: SpamDetectionResult,
        duplicate_message_ids: tuple[int, ...],
        logger: logging.Logger | None = None,
    ) -> SpamDetectionResult:
        return await delete_duplicate_flood_during_grace(
            bot=bot,
            message=message,
            spam_result=spam_result,
            duplicate_message_ids=duplicate_message_ids,
            logger=logger,
        )

    async def kick_duplicate_flood(
        self,
        *,
        bot: Any,
        message: Any,
        spam_result: SpamDetectionResult,
        duplicate_message_ids: tuple[int, ...],
        logger: logging.Logger | None = None,
    ) -> SpamDetectionResult:
        return await kick_duplicate_flood(
            bot=bot,
            message=message,
            spam_result=spam_result,
            duplicate_message_ids=duplicate_message_ids,
            logger=logger,
        )
