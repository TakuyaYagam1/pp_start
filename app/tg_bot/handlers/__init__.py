from app.tg_bot.handlers.admin import router as admin_router
from app.tg_bot.handlers.moderation import router as moderation_router
from app.tg_bot.handlers.user import router as user_router
from app.tg_bot.handlers.verification import (
    router as verification_router,
    verification_timeout_tasks,
)

__all__ = (
    "admin_router",
    "moderation_router",
    "user_router",
    "verification_router",
    "verification_timeout_tasks",
)
