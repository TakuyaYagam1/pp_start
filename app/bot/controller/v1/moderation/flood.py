"""Duplicate flood orchestration for Telegram moderation controller"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram.types import Message

from app.config import Settings
from app.domain import LLMDecision, SpamDetectionResult, StopWordCheckResult
from app.usecase.contract import AutoDeleteMessageStore, DuplicateMessageStore
from app.usecase.moderation import ModerationService
from app.usecase.moderation.auto_delete import AutoDeleteTaskRegistry


@dataclass(frozen=True)
class DuplicateFloodMessageContext:
    chat_id: int
    user_id: int
    message_id: int


async def handle_duplicate_flood(
    *,
    message: Message,
    duplicate_message_repository: DuplicateMessageStore | None,
    settings: Settings | None,
    moderation_service: ModerationService | None,
    bot: Any | None,
    content_key: str | None,
    auto_delete_message_repository: AutoDeleteMessageStore | None = None,
    auto_delete_task_registry: AutoDeleteTaskRegistry | None = None,
    is_exempt_sender: Callable[[], Awaitable[bool]] | None = None,
) -> SpamDetectionResult | None:
    if (
        duplicate_message_repository is None
        or settings is None
        or moderation_service is None
        or bot is None
        or content_key is None
    ):
        return None

    message_context = build_duplicate_flood_message_context(message)
    if message_context is None:
        return None

    state = await duplicate_message_repository.record_message(
        chat_id=message_context.chat_id,
        user_id=message_context.user_id,
        message_id=message_context.message_id,
        content_key=content_key,
    )
    warned_digest = await duplicate_message_repository.get_warning_digest(
        chat_id=message_context.chat_id,
        user_id=message_context.user_id,
    )
    if (
        warned_digest is not None
        and len(state.message_ids) >= settings.duplicate_message_warn_threshold
    ):
        if await clear_duplicate_state_when_sender_is_exempt(
            duplicate_message_repository=duplicate_message_repository,
            message_context=message_context,
            is_exempt_sender=is_exempt_sender,
        ):
            return None

        return await apply_duplicate_flood_after_warning(
            message=message,
            duplicate_message_repository=duplicate_message_repository,
            moderation_service=moderation_service,
            bot=bot,
            duplicate_message_ids=state.message_ids,
            message_context=message_context,
        )

    if len(state.message_ids) < settings.duplicate_message_warn_threshold:
        return None

    if await clear_duplicate_state_when_sender_is_exempt(
        duplicate_message_repository=duplicate_message_repository,
        message_context=message_context,
        is_exempt_sender=is_exempt_sender,
    ):
        return None

    marked_warning = await duplicate_message_repository.mark_warned_once(
        chat_id=message_context.chat_id,
        user_id=message_context.user_id,
        digest=state.digest,
    )
    if not marked_warning:
        warned_digest = await duplicate_message_repository.get_warning_digest(
            chat_id=message_context.chat_id,
            user_id=message_context.user_id,
        )
        if warned_digest is not None:
            return await apply_duplicate_flood_after_warning(
                message=message,
                duplicate_message_repository=duplicate_message_repository,
                moderation_service=moderation_service,
                bot=bot,
                duplicate_message_ids=state.message_ids,
                message_context=message_context,
            )
        return None

    result = SpamDetectionResult(
        is_spam=True,
        reason="duplicate_flood",
        stop_word=StopWordCheckResult(matched=False),
        llm_decision=LLMDecision.UNKNOWN,
        matched_term="duplicate_content",
    )
    try:
        moderation_result = await moderation_service.warn_duplicate_flood(
            bot=bot,
            message=message,
            spam_result=result,
            duplicate_message_ids=state.message_ids,
            warning_message_ttl_seconds=settings.duplicate_warning_message_ttl_seconds,
            auto_delete_message_repository=auto_delete_message_repository,
            auto_delete_task_registry=auto_delete_task_registry,
        )
    except Exception:
        await duplicate_message_repository.clear_warning(
            chat_id=message_context.chat_id,
            user_id=message_context.user_id,
        )
        raise
    await duplicate_message_repository.clear(
        chat_id=message_context.chat_id,
        user_id=message_context.user_id,
    )
    return moderation_result


def build_duplicate_flood_message_context(
    message: Message,
) -> DuplicateFloodMessageContext | None:
    message_id = getattr(message, "message_id", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if message_id is None or chat_id is None or user_id is None:
        return None
    return DuplicateFloodMessageContext(
        chat_id=int(chat_id),
        user_id=int(user_id),
        message_id=int(message_id),
    )


async def clear_duplicate_state_when_sender_is_exempt(
    *,
    duplicate_message_repository: DuplicateMessageStore,
    message_context: DuplicateFloodMessageContext,
    is_exempt_sender: Callable[[], Awaitable[bool]] | None,
) -> bool:
    if not await sender_is_exempt(is_exempt_sender):
        return False

    await clear_duplicate_moderation_state(
        duplicate_message_repository=duplicate_message_repository,
        chat_id=message_context.chat_id,
        user_id=message_context.user_id,
    )
    return True


async def apply_duplicate_flood_after_warning(
    *,
    message: Message,
    duplicate_message_repository: DuplicateMessageStore,
    moderation_service: ModerationService,
    bot: Any,
    duplicate_message_ids: tuple[int, ...],
    message_context: DuplicateFloodMessageContext,
) -> SpamDetectionResult:
    if await duplicate_message_repository.has_warning_grace(
        chat_id=message_context.chat_id,
        user_id=message_context.user_id,
    ):
        return await delete_duplicate_flood_during_grace(
            message=message,
            duplicate_message_repository=duplicate_message_repository,
            moderation_service=moderation_service,
            bot=bot,
            duplicate_message_ids=duplicate_message_ids,
            chat_id=message_context.chat_id,
            user_id=message_context.user_id,
        )
    return await kick_repeated_duplicate_flood(
        message=message,
        duplicate_message_repository=duplicate_message_repository,
        moderation_service=moderation_service,
        bot=bot,
        duplicate_message_ids=duplicate_message_ids,
        chat_id=message_context.chat_id,
        user_id=message_context.user_id,
    )


async def sender_is_exempt(
    is_exempt_sender: Callable[[], Awaitable[bool]] | None,
) -> bool:
    return is_exempt_sender is not None and await is_exempt_sender()


async def clear_duplicate_moderation_state(
    *,
    duplicate_message_repository: DuplicateMessageStore,
    chat_id: int,
    user_id: int,
) -> None:
    await duplicate_message_repository.clear(chat_id=chat_id, user_id=user_id)
    await duplicate_message_repository.clear_warning(chat_id=chat_id, user_id=user_id)


async def delete_duplicate_flood_during_grace(
    *,
    message: Message,
    duplicate_message_repository: DuplicateMessageStore,
    moderation_service: ModerationService,
    bot: Any,
    duplicate_message_ids: tuple[int, ...],
    chat_id: int,
    user_id: int,
) -> SpamDetectionResult:
    result = SpamDetectionResult(
        is_spam=True,
        reason="duplicate_flood_warning_grace",
        stop_word=StopWordCheckResult(matched=False),
        llm_decision=LLMDecision.UNKNOWN,
        matched_term="duplicate_content",
    )
    moderation_result = await moderation_service.delete_duplicate_flood_during_grace(
        bot=bot,
        message=message,
        spam_result=result,
        duplicate_message_ids=duplicate_message_ids,
    )
    await duplicate_message_repository.clear(chat_id=chat_id, user_id=user_id)
    return moderation_result


async def kick_repeated_duplicate_flood(
    *,
    message: Message,
    duplicate_message_repository: DuplicateMessageStore,
    moderation_service: ModerationService,
    bot: Any,
    duplicate_message_ids: tuple[int, ...],
    chat_id: int,
    user_id: int,
) -> SpamDetectionResult:
    result = SpamDetectionResult(
        is_spam=True,
        reason="duplicate_flood_repeated_after_warning",
        stop_word=StopWordCheckResult(matched=False),
        llm_decision=LLMDecision.SPAM,
        matched_term="duplicate_content",
    )
    moderation_result = await moderation_service.kick_duplicate_flood(
        bot=bot,
        message=message,
        spam_result=result,
        duplicate_message_ids=duplicate_message_ids,
    )
    await duplicate_message_repository.clear(chat_id=chat_id, user_id=user_id)
    await duplicate_message_repository.clear_warning(chat_id=chat_id, user_id=user_id)
    return moderation_result
