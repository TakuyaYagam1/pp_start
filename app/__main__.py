from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, Dispatcher

from app.cache.redis import (
    BlacklistRepository,
    LLMResultCacheRepository,
    PendingVerificationRepository,
    RuntimeSettingsRepository,
    VerifiedUserRepository,
    create_redis_client,
)
from app.config import Settings
from app.core.llm.client import LLMClient
from app.core.services.moderation import ModerationService
from app.core.services.spam_detector import SpamDetectorService
from app.core.services.verification import schedule_join_request_timeout
from app.logging import configure_logging, get_logger, log_app_event
from app.tg_bot.handlers import (
    admin_router,
    user_router,
    verification_timeout_tasks,
)
from app.tg_bot.middlewares import RedisMiddleware


ALLOWED_UPDATES: tuple[str, ...] = ("message", "callback_query", "chat_join_request")
RESTORED_TIMER_SAFETY_MARGIN_SECONDS = 0.25


@dataclass(frozen=True)
class BotApplication:
    bot: Any
    dispatcher: Dispatcher
    redis_client: Any
    settings: Settings
    allowed_updates: tuple[str, ...] = ALLOWED_UPDATES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description="Run the anti-spam Telegram bot.",
    )
    return parser


def include_application_router(dispatcher: Dispatcher, router: Any) -> None:
    if getattr(router, "parent_router", None) is not None:
        # Module-level routers retain their parent; reset for repeatable factories
        router._parent_router = None
    dispatcher.include_router(router)


def _redis_key_text(key: Any) -> str:
    if isinstance(key, bytes):
        return key.decode("utf-8", errors="replace")
    return str(key)


def _parse_pending_verification_key(key: str) -> tuple[int, int] | None:
    parts = key.split(":")
    if len(parts) != 3 or parts[0] != "verify":
        return None

    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _restored_timer_delay(ttl_seconds: int) -> float:
    if ttl_seconds <= 1:
        return 0
    return max(0, ttl_seconds - RESTORED_TIMER_SAFETY_MARGIN_SECONDS)


async def _delete_unrestorable_verification_key(
    *,
    redis_client: Any,
    key: str,
    reason: str,
    logger: logging.Logger,
) -> None:
    await redis_client.delete(key)
    log_app_event(
        logger,
        event="pending_verification_restore_skipped",
        action="delete_pending_verification",
        details=f"key={key}; reason={reason}",
    )


async def restore_pending_verification_timers(
    *,
    redis_client: Any,
    bot: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> int:
    event_logger = logger or get_logger("app")
    restored = 0
    async for raw_key in redis_client.scan_iter(match="verify:*"):
        key = _redis_key_text(raw_key)
        parsed_key = _parse_pending_verification_key(key)
        if parsed_key is None:
            await _delete_unrestorable_verification_key(
                redis_client=redis_client,
                key=key,
                reason="invalid_key",
                logger=event_logger,
            )
            continue
        chat_id, user_id = parsed_key

        ttl = await redis_client.ttl(key)
        if ttl <= 0:
            await _delete_unrestorable_verification_key(
                redis_client=redis_client,
                key=key,
                reason=f"invalid_ttl:{ttl}",
                logger=event_logger,
            )
            continue

        try:
            pending = await pending_verification_repository.get(
                chat_id=chat_id,
                user_id=user_id,
            )
        except KeyError, TypeError, ValueError:
            await _delete_unrestorable_verification_key(
                redis_client=redis_client,
                key=key,
                reason="invalid_payload",
                logger=event_logger,
            )
            continue

        if pending is None:
            log_app_event(
                event_logger,
                event="pending_verification_restore_skipped",
                chat_id=chat_id,
                user_id=user_id,
                action="skip_pending_verification",
                details="pending record disappeared before timer restore",
            )
            continue

        if pending.chat_id != chat_id or pending.user_id != user_id:
            await _delete_unrestorable_verification_key(
                redis_client=redis_client,
                key=key,
                reason="payload_key_mismatch",
                logger=event_logger,
            )
            continue

        schedule_join_request_timeout(
            bot=bot,
            pending_verification_repository=pending_verification_repository,
            blacklist_repository=blacklist_repository,
            chat_id=chat_id,
            user_id=user_id,
            timeout_seconds=_restored_timer_delay(
                min(ttl, settings.verify_timeout_seconds)
            ),
            task_registry=verification_timeout_tasks,
            logger=event_logger,
        )
        restored += 1
    return restored


async def on_startup(
    *,
    bot: Any,
    redis_client: Any,
    pending_verification_repository: PendingVerificationRepository,
    blacklist_repository: BlacklistRepository,
    settings: Settings,
    logger: logging.Logger | None = None,
    **_: Any,
) -> None:
    await redis_client.ping()
    await restore_pending_verification_timers(
        redis_client=redis_client,
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        blacklist_repository=blacklist_repository,
        settings=settings,
        logger=logger,
    )


async def on_shutdown(*, bot: Any, redis_client: Any, **_: Any) -> None:
    await bot.session.close()
    try:
        await redis_client.aclose(close_connection_pool=True)
    except TypeError:
        await redis_client.aclose()


def create_application(
    settings: Settings | None = None,
    *,
    bot_factory: Callable[..., Any] = Bot,
    dispatcher_factory: Callable[[], Dispatcher] = Dispatcher,
    redis_client: Any | None = None,
) -> BotApplication:
    resolved_settings = settings or Settings()
    logger = configure_logging(resolved_settings)

    bot = bot_factory(token=resolved_settings.bot_token.get_secret_value())
    dispatcher = dispatcher_factory()
    redis = redis_client or create_redis_client(resolved_settings.redis_url)

    pending_verification_repository = PendingVerificationRepository(
        redis,
        ttl_seconds=resolved_settings.verify_timeout_seconds,
    )
    verified_user_repository = VerifiedUserRepository(redis)
    blacklist_repository = BlacklistRepository(redis)
    runtime_settings_repository = RuntimeSettingsRepository(redis)
    llm_cache_repository = LLMResultCacheRepository(redis)
    llm_client = LLMClient.from_settings(resolved_settings)
    spam_detector_service = SpamDetectorService(
        llm_client=llm_client,
        llm_cache_repository=llm_cache_repository,
    )
    moderation_service = ModerationService()

    dispatcher.update.outer_middleware(RedisMiddleware(redis))
    include_application_router(dispatcher, admin_router)
    include_application_router(dispatcher, user_router)
    dispatcher.workflow_data.update(
        {
            "settings": resolved_settings,
            "logger": logger,
            "redis_client": redis,
            "pending_verification_repository": pending_verification_repository,
            "verified_user_repository": verified_user_repository,
            "blacklist_repository": blacklist_repository,
            "runtime_settings_repository": runtime_settings_repository,
            "llm_client": llm_client,
            "llm_cache_repository": llm_cache_repository,
            "spam_detector_service": spam_detector_service,
            "moderation_service": moderation_service,
        }
    )
    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)

    return BotApplication(
        bot=bot,
        dispatcher=dispatcher,
        redis_client=redis,
        settings=resolved_settings,
    )


async def run_polling() -> None:
    application = create_application()
    await application.dispatcher.start_polling(
        application.bot,
        allowed_updates=list(application.allowed_updates),
    )


def main() -> None:
    build_parser().parse_args()
    asyncio.run(run_polling())


if __name__ == "__main__":
    main()
