"""Startup and shutdown hooks for runtime resources"""

from __future__ import annotations

import logging
from typing import Any

from app.bootstrap.command import set_bot_commands
from app.bootstrap.verification_timer import restore_pending_verification_timer
from app.config import Settings
from app.infrastructure.redis import (
    AutoDeleteMessageRepository,
    PendingVerificationRepository,
    VerifiedUserRepository,
)
from app.observability.logging import get_logger, log_app_event
from app.usecase.moderation import (
    AutoDeleteTaskRegistry,
    restore_auto_delete_message_tasks,
)
from app.usecase.verification import VerificationTaskRegistry


async def on_startup(
    *,
    bot: Any,
    redis_client: Any,
    pending_verification_repository: PendingVerificationRepository,
    verified_user_repository: VerifiedUserRepository,
    verification_task_registry: VerificationTaskRegistry,
    auto_delete_message_repository: AutoDeleteMessageRepository,
    auto_delete_task_registry: AutoDeleteTaskRegistry,
    settings: Settings,
    logger: logging.Logger | None = None,
    **_: Any,
) -> None:
    await redis_client.ping()
    await set_bot_commands_best_effort(bot=bot, settings=settings, logger=logger)
    await restore_pending_verification_timer(
        redis_client=redis_client,
        bot=bot,
        pending_verification_repository=pending_verification_repository,
        verified_user_repository=verified_user_repository,
        verification_task_registry=verification_task_registry,
        settings=settings,
        logger=logger,
    )
    await restore_auto_delete_message_tasks(
        bot=bot,
        auto_delete_message_repository=auto_delete_message_repository,
        auto_delete_task_registry=auto_delete_task_registry,
        logger=logger,
    )


async def set_bot_commands_best_effort(
    *,
    bot: Any,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> None:
    try:
        await set_bot_commands(bot, settings)
    except Exception as exc:
        log_app_event(
            logger or get_logger("app"),
            event="bot_commands_registration_failed",
            action="set_bot_commands",
            details=f"error_type={type(exc).__name__}",
            level=logging.ERROR,
        )


async def on_shutdown(
    *,
    bot: Any,
    redis_client: Any,
    verification_task_registry: VerificationTaskRegistry,
    auto_delete_task_registry: AutoDeleteTaskRegistry,
    llm_client: Any | None = None,
    logger: logging.Logger | None = None,
    **_: Any,
) -> None:
    await verification_task_registry.cancel_all()
    await auto_delete_task_registry.cancel_all()
    close_errors: list[Exception] = []

    if llm_client is not None:
        close = getattr(llm_client, "aclose", None)
        if callable(close):
            await close_shutdown_resource(
                name="llm_client",
                close_call=close(),
                errors=close_errors,
                logger=logger,
            )

    await close_shutdown_resource(
        name="bot_session",
        close_call=bot.session.close(),
        errors=close_errors,
        logger=logger,
    )
    try:
        redis_close_call = redis_client.aclose(close_connection_pool=True)
    except TypeError:
        redis_close_call = redis_client.aclose()
    await close_shutdown_resource(
        name="redis_client",
        close_call=redis_close_call,
        errors=close_errors,
        logger=logger,
    )

    if close_errors:
        raise RuntimeError("shutdown cleanup failed") from close_errors[0]


async def close_shutdown_resource(
    *,
    name: str,
    close_call: Any,
    errors: list[Exception],
    logger: logging.Logger | None,
) -> None:
    try:
        await close_call
    except Exception as exc:
        errors.append(exc)
        if logger is not None:
            logger.exception("shutdown resource close failed", extra={"resource": name})
