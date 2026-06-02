from aiogram import Router

from app.tg_bot.handlers.moderation import router as moderation_router
from app.tg_bot.handlers.verification import router as verification_router

router = Router(name="user")
router.include_router(verification_router)
router.include_router(moderation_router)

__all__ = ("router",)
