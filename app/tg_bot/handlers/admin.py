from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.cache.redis import RuntimeSettingsRepository
from app.config import Settings
from app.core.models import ActionMode

router = Router(name="admin")

ACTION_MODE_CALLBACK_PREFIX = "admin_mode"
ACTION_MODE_ALIASES = {
    "delete": ActionMode.DELETE,
    "notify": ActionMode.NOTIFY_ADMIN,
    "notify_admin": ActionMode.NOTIFY_ADMIN,
}
ACTION_MODE_RESET_ALIASES = {"default", "env", "reset"}


def build_action_mode_keyboard(*, current_mode: ActionMode) -> InlineKeyboardMarkup:
    delete_text = (
        "✅ Удалять спам" if current_mode == ActionMode.DELETE else "Удалять спам"
    )
    notify_text = (
        "✅ Только уведомлять"
        if current_mode == ActionMode.NOTIFY_ADMIN
        else "Только уведомлять"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=delete_text,
                    callback_data=f"{ACTION_MODE_CALLBACK_PREFIX}:delete",
                ),
                InlineKeyboardButton(
                    text=notify_text,
                    callback_data=f"{ACTION_MODE_CALLBACK_PREFIX}:notify_admin",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Сбросить к env",
                    callback_data=f"{ACTION_MODE_CALLBACK_PREFIX}:reset",
                )
            ],
        ]
    )


def build_admin_panel_text(*, current_mode: ActionMode) -> str:
    return (
        "⚙️ Панель администратора\n\n"
        f"Текущий режим модерации: {current_mode.value}\n\n"
        "Команды:\n"
        "/mode\n"
        "/mode delete\n"
        "/mode notify_admin\n"
        "/mode reset"
    )


def _normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    return username.strip().lstrip("@").casefold() or None


def _has_admin_target(settings: Settings) -> bool:
    return bool(settings.admin_username or settings.admin_id is not None)


def is_admin_sender(*, message: Any, settings: Settings) -> bool:
    sender = getattr(message, "from_user", None)
    if sender is None:
        return False

    sender_id = getattr(sender, "id", None)
    if settings.admin_id is not None and sender_id is not None:
        if int(sender_id) == settings.admin_id:
            return True

    expected_username = _normalize_username(settings.admin_username)
    sender_username = _normalize_username(getattr(sender, "username", None))
    return expected_username is not None and expected_username == sender_username


def parse_action_mode_argument(text: str | None) -> str | None:
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        return None
    return parts[1].split(maxsplit=1)[0].casefold()


async def handle_action_mode_command(
    *,
    message: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
) -> ActionMode | None:
    if not is_admin_sender(message=message, settings=settings):
        await message.answer("❌ Недостаточно прав для изменения режима модерации")
        return None

    argument = parse_action_mode_argument(getattr(message, "text", None))
    if argument is None:
        current_mode = await runtime_settings_repository.get_action_mode(
            default=settings.action_mode
        )
        await message.answer(
            build_admin_panel_text(current_mode=current_mode),
            reply_markup=build_action_mode_keyboard(current_mode=current_mode),
        )
        return current_mode

    if argument in ACTION_MODE_RESET_ALIASES:
        await runtime_settings_repository.reset_action_mode()
        await message.answer(
            f"✅ Runtime-режим сброшен. Активен режим из env: {settings.action_mode.value}",
            reply_markup=build_action_mode_keyboard(current_mode=settings.action_mode),
        )
        return settings.action_mode

    action_mode = ACTION_MODE_ALIASES.get(argument)
    if action_mode is None:
        await message.answer("❌ Неверный режим. Доступно: delete или notify_admin")
        return None

    if action_mode == ActionMode.NOTIFY_ADMIN and not _has_admin_target(settings):
        await message.answer(
            "⚠️ Для notify_admin нужно задать ADMIN_USERNAME или ADMIN_ID"
        )
        return None

    await runtime_settings_repository.set_action_mode(action_mode)
    await message.answer(
        f"✅ Режим модерации изменен: {action_mode.value}",
        reply_markup=build_action_mode_keyboard(current_mode=action_mode),
    )
    return action_mode


async def handle_admin_panel_command(
    *,
    message: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
) -> ActionMode | None:
    if not is_admin_sender(message=message, settings=settings):
        await message.answer("❌ Недостаточно прав для панели администратора")
        return None

    current_mode = await runtime_settings_repository.get_action_mode(
        default=settings.action_mode
    )
    await message.answer(
        build_admin_panel_text(current_mode=current_mode),
        reply_markup=build_action_mode_keyboard(current_mode=current_mode),
    )
    return current_mode


async def handle_action_mode_callback(
    *,
    callback_query: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
) -> ActionMode | None:
    if not is_admin_sender(message=callback_query, settings=settings):
        await callback_query.answer(
            text="❌ Недостаточно прав для изменения режима",
            show_alert=True,
        )
        return None

    data = str(getattr(callback_query, "data", ""))
    argument = data.removeprefix(f"{ACTION_MODE_CALLBACK_PREFIX}:").casefold()

    if argument in ACTION_MODE_RESET_ALIASES:
        await runtime_settings_repository.reset_action_mode()
        current_mode = settings.action_mode
        answer_text = f"✅ Активен режим из env: {current_mode.value}"
    else:
        action_mode = ACTION_MODE_ALIASES.get(argument)
        if action_mode is None:
            await callback_query.answer(text="❌ Неверный режим", show_alert=True)
            return None

        if action_mode == ActionMode.NOTIFY_ADMIN and not _has_admin_target(settings):
            await callback_query.answer(
                text="⚠️ Нужно задать ADMIN_USERNAME или ADMIN_ID",
                show_alert=True,
            )
            return None

        await runtime_settings_repository.set_action_mode(action_mode)
        current_mode = action_mode
        answer_text = f"✅ Режим изменен: {current_mode.value}"

    message = getattr(callback_query, "message", None)
    if message is not None:
        try:
            await message.edit_text(
                build_admin_panel_text(current_mode=current_mode),
                reply_markup=build_action_mode_keyboard(current_mode=current_mode),
            )
        except Exception:
            pass
    await callback_query.answer(text=answer_text)
    return current_mode


@router.message(Command("mode"))
async def on_action_mode_command(
    message: Message,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
) -> None:
    await handle_action_mode_command(
        message=message,
        settings=settings,
        runtime_settings_repository=runtime_settings_repository,
    )


@router.message(Command("admin"))
@router.message(Command("help"))
async def on_admin_panel_command(
    message: Message,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
) -> None:
    await handle_admin_panel_command(
        message=message,
        settings=settings,
        runtime_settings_repository=runtime_settings_repository,
    )


@router.callback_query(F.data.startswith(f"{ACTION_MODE_CALLBACK_PREFIX}:"))
async def on_action_mode_callback(
    callback_query: CallbackQuery,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
) -> None:
    await handle_action_mode_callback(
        callback_query=callback_query,
        settings=settings,
        runtime_settings_repository=runtime_settings_repository,
    )


__all__ = ("router",)
