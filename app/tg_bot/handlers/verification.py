from __future__ import annotations

import asyncio
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, ChatJoinRequest

from app.cache.redis import (
    BlacklistRepository,
    PendingVerificationRepository,
    VerifiedUserRepository,
)
from app.config import Settings
from app.core.services.verification import (
    VERIFY_CALLBACK_PREFIX,
    complete_verification_from_callback,
    start_join_request_verification,
)
from app.logging import get_logger


router = Router(name="verification")
verification_timeout_tasks: dict[tuple[int, int], asyncio.Task[bool]] = {}


async def handle_chat_join_request(
    *,
    join_request: ChatJoinRequest,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    settings: Settings,
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
        task_registry=verification_timeout_tasks,
        logger=get_logger("app"),
    )


async def on_chat_join_request(
    join_request: ChatJoinRequest,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    settings: Settings,
) -> None:
    await handle_chat_join_request(
        join_request=join_request,
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        blacklist_repository=blacklist_repository,
        settings=settings,
    )


router.observers["chat_join_request"].register(on_chat_join_request)


@router.callback_query(F.data.startswith(f"{VERIFY_CALLBACK_PREFIX}:"))
async def on_verify_callback(
    callback_query: CallbackQuery,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    verified_user_repository: VerifiedUserRepository,
) -> None:
    await complete_verification_from_callback(
        callback_query=callback_query,
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        verified_user_repository=verified_user_repository,
        task_registry=verification_timeout_tasks,
    )
