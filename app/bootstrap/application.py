"""Application factory that wires dependencies and aiogram routers"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from app.bootstrap.lifecycle import on_shutdown, on_startup
from app.bootstrap.verification_timer import pending_verification_ttl_seconds
from app.bot.controller.v1 import admin_router, user_router
from app.bot.middleware import RedisMiddleware
from app.config import Settings
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.redis import (
    AutoDeleteMessageRepository,
    DuplicateMessageRepository,
    LLMResultCacheRepository,
    PendingVerificationRepository,
    RuntimeSettingsRepository,
    StopWordWarningRepository,
    VerifiedUserRepository,
    create_redis_client,
)
from app.observability.logging import configure_logging
from app.usecase.moderation import AutoDeleteTaskRegistry, ModerationService
from app.usecase.moderation.spam_detector import SpamDetectorService
from app.usecase.verification import VerificationTaskRegistry

ALLOWED_UPDATES: tuple[str, ...] = (
    "message",
    "callback_query",
    "chat_join_request",
    "chat_member",
)


@dataclass(frozen=True)
class BotApplication:
    bot: Any
    dispatcher: Dispatcher
    redis_client: Any
    settings: Settings
    verification_task_registry: VerificationTaskRegistry
    auto_delete_task_registry: AutoDeleteTaskRegistry
    allowed_updates: tuple[str, ...] = ALLOWED_UPDATES


def include_application_router(dispatcher: Dispatcher, router: Any) -> None:
    if getattr(router, "parent_router", None) is not None:
        router._parent_router = None
    dispatcher.include_router(router)


def create_bot_session(settings: Settings) -> AiohttpSession | None:
    if settings.telegram_proxy_url is None:
        return None
    return AiohttpSession(proxy=settings.telegram_proxy_url)


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def create_application(
    settings: Settings | None = None,
    *,
    bot_factory: Callable[..., Any] = Bot,
    dispatcher_factory: Callable[[], Dispatcher] = Dispatcher,
    redis_client: Any | None = None,
) -> BotApplication:
    resolved_settings = settings or load_settings()
    logger = configure_logging(resolved_settings)

    bot_session = create_bot_session(resolved_settings)
    bot_kwargs: dict[str, Any] = {
        "token": resolved_settings.bot_token.get_secret_value()
    }
    if bot_session is not None:
        bot_kwargs["session"] = bot_session
    bot = bot_factory(**bot_kwargs)
    dispatcher = dispatcher_factory()
    redis = redis_client or create_redis_client(resolved_settings.redis_url)

    pending_verification_repository = PendingVerificationRepository(
        redis,
        ttl_seconds=pending_verification_ttl_seconds(resolved_settings),
    )
    verified_user_repository = VerifiedUserRepository(redis)
    runtime_settings_repository = RuntimeSettingsRepository(redis)
    duplicate_message_repository = DuplicateMessageRepository(
        redis,
        ttl_seconds=resolved_settings.duplicate_message_window_seconds,
        warning_ttl_seconds=resolved_settings.duplicate_message_warning_ttl_seconds,
        warning_grace_seconds=resolved_settings.duplicate_message_kick_grace_seconds,
    )
    stop_word_warning_repository = StopWordWarningRepository(
        redis,
        ttl_seconds=resolved_settings.stop_word_warning_ttl_seconds,
    )
    auto_delete_message_repository = AutoDeleteMessageRepository(
        redis,
        cleanup_grace_seconds=(
            resolved_settings.auto_delete_message_cleanup_grace_seconds
        ),
    )
    llm_cache_repository = LLMResultCacheRepository(
        redis,
        ttl_seconds=resolved_settings.llm_cache_ttl_seconds,
    )
    llm_client = LLMClient.from_settings(resolved_settings)
    spam_detector_service = SpamDetectorService(
        llm_client=llm_client,
        llm_cache_repository=llm_cache_repository,
    )
    moderation_service = ModerationService()
    verification_task_registry = VerificationTaskRegistry()
    auto_delete_task_registry = AutoDeleteTaskRegistry()

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
            "runtime_settings_repository": runtime_settings_repository,
            "duplicate_message_repository": duplicate_message_repository,
            "stop_word_warning_repository": stop_word_warning_repository,
            "auto_delete_message_repository": auto_delete_message_repository,
            "llm_client": llm_client,
            "llm_cache_repository": llm_cache_repository,
            "spam_detector_service": spam_detector_service,
            "moderation_service": moderation_service,
            "verification_task_registry": verification_task_registry,
            "auto_delete_task_registry": auto_delete_task_registry,
        }
    )
    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)

    return BotApplication(
        bot=bot,
        dispatcher=dispatcher,
        redis_client=redis,
        settings=resolved_settings,
        verification_task_registry=verification_task_registry,
        auto_delete_task_registry=auto_delete_task_registry,
    )


async def run_polling() -> None:
    application = create_application()
    await application.dispatcher.start_polling(
        application.bot,
        allowed_updates=list(application.allowed_updates),
    )
