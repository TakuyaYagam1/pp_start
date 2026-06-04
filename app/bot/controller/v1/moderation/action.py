"""Moderation action mode application for Telegram controller"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.types import Message

from app.config import Settings
from app.domain import ActionMode, SpamDetectionResult
from app.usecase.contract import (
    AutoDeleteMessageStore,
    RuntimeSettingsStore,
    StopWordWarningStore,
)
from app.usecase.moderation import ModerationService
from app.usecase.moderation.auto_delete import AutoDeleteTaskRegistry


async def apply_stop_word_warning(
    *,
    message: Message,
    result: SpamDetectionResult,
    settings: Settings,
    moderation_service: ModerationService,
    stop_word_warning_repository: StopWordWarningStore | None,
    auto_delete_message_repository: AutoDeleteMessageStore | None,
    auto_delete_task_registry: AutoDeleteTaskRegistry | None,
    bot: Any,
) -> SpamDetectionResult | None:
    matched_term = result.matched_term or result.stop_word.matched_term
    if (
        not result.is_spam
        or not result.stop_word.matched
        or matched_term is None
        or stop_word_warning_repository is None
    ):
        return None

    chat_id = getattr(getattr(message, "chat", None), "id", None)
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if chat_id is None or user_id is None:
        return None

    marked_warning = await stop_word_warning_repository.mark_warned_once(
        chat_id=int(chat_id),
        user_id=int(user_id),
        matched_term=matched_term,
    )
    if not marked_warning:
        return None

    try:
        return await moderation_service.warn_stop_word_spam(
            bot=bot,
            message=message,
            spam_result=result,
            warning_message_ttl_seconds=settings.stop_word_warning_message_ttl_seconds,
            auto_delete_message_repository=auto_delete_message_repository,
            auto_delete_task_registry=auto_delete_task_registry,
        )
    except Exception:
        await stop_word_warning_repository.clear(
            chat_id=int(chat_id),
            user_id=int(user_id),
        )
        raise


async def apply_moderation_action(
    *,
    message: Message,
    result: SpamDetectionResult,
    settings: Settings | None,
    moderation_service: ModerationService | None,
    runtime_settings_repository: RuntimeSettingsStore | None,
    stop_word_warning_repository: StopWordWarningStore | None,
    auto_delete_message_repository: AutoDeleteMessageStore | None,
    auto_delete_task_registry: AutoDeleteTaskRegistry | None,
    is_exempt_sender: Callable[[], Awaitable[bool]] | None = None,
    bot: Any | None,
) -> SpamDetectionResult:
    if (
        not result.is_spam
        or settings is None
        or moderation_service is None
        or bot is None
    ):
        return result

    if is_exempt_sender is not None and await is_exempt_sender():
        return result

    action_mode = settings.action_mode
    if runtime_settings_repository is not None:
        action_mode = await runtime_settings_repository.get_action_mode(
            default=settings.action_mode,
            chat_id=int(message.chat.id),
        )

    if action_mode == ActionMode.DELETE:
        warning_result = await apply_stop_word_warning(
            message=message,
            result=result,
            settings=settings,
            moderation_service=moderation_service,
            stop_word_warning_repository=stop_word_warning_repository,
            auto_delete_message_repository=auto_delete_message_repository,
            auto_delete_task_registry=auto_delete_task_registry,
            bot=bot,
        )
        if warning_result is not None:
            return warning_result

        return await moderation_service.delete_spam_message(
            bot=bot,
            message=message,
            spam_result=result,
        )

    if action_mode == ActionMode.NOTIFY_ADMIN:
        notification_target = None
        if runtime_settings_repository is not None:
            notification_target = (
                await runtime_settings_repository.get_notification_target(
                    chat_id=int(message.chat.id)
                )
            )
        return await moderation_service.notify_admin_about_spam(
            bot=bot,
            message=message,
            spam_result=result,
            settings=settings,
            notification_target=notification_target,
        )
