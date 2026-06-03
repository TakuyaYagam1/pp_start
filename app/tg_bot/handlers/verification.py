from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, ChatJoinRequest, ChatMemberUpdated

from app.cache.redis import (
    BlacklistRepository,
    PendingVerificationRepository,
    VerifiedUserRepository,
)
from app.config import Settings
from app.core.services.verification import (
    VERIFY_CALLBACK_PREFIX,
    VerificationTaskRegistries,
    complete_verification_from_callback,
    start_join_request_verification,
    start_member_verification,
)
from app.observability.logging import get_logger


router = Router(name="verification")
JOINED_MEMBER_STATUSES = {"member", "restricted"}
PRE_JOIN_STATUSES = {"left", "kicked"}


def _member_status(member: object) -> str:
    status = getattr(member, "status", "")
    return str(getattr(status, "value", status)).lower()


def _joined_from_outside(update: ChatMemberUpdated) -> bool:
    old_status = _member_status(update.old_chat_member)
    new_status = _member_status(update.new_chat_member)
    return old_status in PRE_JOIN_STATUSES and new_status in JOINED_MEMBER_STATUSES


async def handle_chat_join_request(
    *,
    join_request: ChatJoinRequest,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    settings: Settings,
    verification_task_registries: VerificationTaskRegistries,
) -> bool:
    user = join_request.from_user
    return await start_join_request_verification(
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        blacklist_repository=blacklist_repository,
        chat_id=join_request.chat.id,
        user_id=user.id,
        user_chat_id=join_request.user_chat_id,
        user_full_name=user.full_name,
        timeout_seconds=settings.verify_timeout_seconds,
        task_registry=verification_task_registries.timeout_tasks,
        countdown_task_registry=verification_task_registries.countdown_tasks,
        logger=get_logger("app"),
    )


async def on_chat_join_request(
    join_request: ChatJoinRequest,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    settings: Settings,
    verification_task_registries: VerificationTaskRegistries,
) -> None:
    await handle_chat_join_request(
        join_request=join_request,
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        blacklist_repository=blacklist_repository,
        settings=settings,
        verification_task_registries=verification_task_registries,
    )


router.observers["chat_join_request"].register(on_chat_join_request)


async def handle_chat_member_update(
    *,
    update: ChatMemberUpdated,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    settings: Settings,
    verification_task_registries: VerificationTaskRegistries,
) -> bool:
    if not _joined_from_outside(update):
        return False

    user = update.new_chat_member.user
    if getattr(user, "is_bot", False):
        return False

    return await start_member_verification(
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        blacklist_repository=blacklist_repository,
        chat_id=update.chat.id,
        user_id=user.id,
        user_full_name=user.full_name,
        timeout_seconds=settings.verify_timeout_seconds,
        task_registry=verification_task_registries.timeout_tasks,
        countdown_task_registry=verification_task_registries.countdown_tasks,
        logger=get_logger("app"),
    )


async def on_chat_member_update(
    update: ChatMemberUpdated,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    settings: Settings,
    verification_task_registries: VerificationTaskRegistries,
) -> None:
    await handle_chat_member_update(
        update=update,
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        blacklist_repository=blacklist_repository,
        settings=settings,
        verification_task_registries=verification_task_registries,
    )


router.observers["chat_member"].register(on_chat_member_update)


@router.callback_query(F.data.startswith(f"{VERIFY_CALLBACK_PREFIX}:"))
async def on_verify_callback(
    callback_query: CallbackQuery,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    verified_user_repository: VerifiedUserRepository,
    verification_task_registries: VerificationTaskRegistries,
) -> None:
    await complete_verification_from_callback(
        callback_query=callback_query,
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        verified_user_repository=verified_user_repository,
        task_registry=verification_task_registries.timeout_tasks,
        countdown_task_registry=verification_task_registries.countdown_tasks,
    )
