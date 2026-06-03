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
NOTIFICATION_TARGET_RESET_ALIASES = {"default", "env", "reset"}
ADMIN_STATUSES = {"administrator", "creator"}
GROUP_CHAT_TYPES = {"group", "supergroup"}


def _action_mode_callback_data(*, action: str, chat_id: int | None = None) -> str:
    if chat_id is None:
        return f"{ACTION_MODE_CALLBACK_PREFIX}:{action}"
    return f"{ACTION_MODE_CALLBACK_PREFIX}:{action}:{chat_id}"


def parse_action_mode_callback_data(data: str) -> tuple[str, int | None]:
    parts = data.split(":")
    if len(parts) < 2 or parts[0] != ACTION_MODE_CALLBACK_PREFIX:
        return "", None

    chat_id = None
    if len(parts) >= 3:
        try:
            chat_id = int(parts[2])
        except ValueError:
            chat_id = None

    return parts[1].casefold(), chat_id


def build_action_mode_keyboard(
    *,
    current_mode: ActionMode,
    chat_id: int | None = None,
) -> InlineKeyboardMarkup:
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
                    callback_data=_action_mode_callback_data(
                        action="delete",
                        chat_id=chat_id,
                    ),
                ),
                InlineKeyboardButton(
                    text=notify_text,
                    callback_data=_action_mode_callback_data(
                        action="notify_admin",
                        chat_id=chat_id,
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Сбросить к env",
                    callback_data=_action_mode_callback_data(
                        action="reset",
                        chat_id=chat_id,
                    ),
                )
            ],
        ]
    )


def build_admin_panel_text(
    *,
    current_mode: ActionMode,
    notification_target: str | None = None,
) -> str:
    target_text = notification_target or "env/default"
    return (
        "⚙️ Панель администратора\n\n"
        f"Текущий режим модерации: {current_mode.value}\n\n"
        f"Получатель уведомлений: {target_text}\n\n"
        "Команды:\n"
        "/mode\n"
        "/mode delete\n"
        "/mode notify_admin\n"
        "/mode reset\n"
        "/notify\n"
        "/notify me\n"
        "/notify @username\n"
        "/notify 123456789\n"
        "/notify reset"
    )


def _normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    return username.strip().lstrip("@").casefold() or None


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


def _sender_id(message: Any) -> int | None:
    sender = getattr(message, "from_user", None)
    sender_id = getattr(sender, "id", None)
    return None if sender_id is None else int(sender_id)


def _chat_id(message: Any) -> int | None:
    chat = getattr(message, "chat", None)
    if chat is None:
        nested_message = getattr(message, "message", None)
        chat = getattr(nested_message, "chat", None)

    raw_chat_id = getattr(chat, "id", None)
    return None if raw_chat_id is None else int(raw_chat_id)


def _is_group_chat(message: Any) -> bool:
    chat = getattr(message, "chat", None)
    if chat is None:
        nested_message = getattr(message, "message", None)
        chat = getattr(nested_message, "chat", None)

    chat_type = str(getattr(chat, "type", "")).lower()
    return chat_type in GROUP_CHAT_TYPES


async def _delete_command_message(message: Any) -> None:
    delete = getattr(message, "delete", None)
    if not callable(delete):
        return

    try:
        await delete()
    except Exception:
        pass


async def _deny_admin_command(*, message: Any, bot: Any | None, text: str) -> None:
    if bot is not None and _is_group_chat(message):
        await _delete_command_message(message)
        return

    await message.answer(text)


async def _send_admin_response(
    *,
    message: Any,
    bot: Any | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if bot is not None and _is_group_chat(message):
        await _delete_command_message(message)
        sender_id = _sender_id(message)
        if sender_id is None:
            return

        try:
            await bot.send_message(
                chat_id=sender_id,
                text=text,
                reply_markup=reply_markup,
            )
        except Exception:
            return
        return

    await message.answer(text, reply_markup=reply_markup)


async def is_authorized_admin_sender(
    *,
    message: Any,
    settings: Settings,
    bot: Any | None = None,
    chat_id: int | None = None,
) -> bool:
    chat_id = chat_id or _chat_id(message)
    sender_id = _sender_id(message)
    if bot is not None and chat_id is not None and sender_id is not None:
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=sender_id)
        except Exception:
            member = None

        status = getattr(member, "status", None)
        status_value = str(getattr(status, "value", status)).lower()
        if status_value in ADMIN_STATUSES:
            return True

    return is_admin_sender(message=message, settings=settings)


def parse_action_mode_argument(text: str | None) -> str | None:
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        return None
    return parts[1].split(maxsplit=1)[0].casefold()


def parse_notification_target_argument(text: str | None) -> str | None:
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        return None
    return parts[1].strip() or None


def _settings_notification_target(settings: Settings) -> str | None:
    if settings.admin_id is not None:
        return str(settings.admin_id)
    if settings.admin_username:
        username = settings.admin_username.strip()
        return username if username.startswith("@") else f"@{username}"
    return None


async def _current_notification_target(
    *,
    message: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
    chat_id: int | None = None,
) -> str | None:
    chat_id = chat_id or _chat_id(message)
    if chat_id is None:
        return _settings_notification_target(settings)

    runtime_target = await runtime_settings_repository.get_notification_target(
        chat_id=chat_id
    )
    return runtime_target or _settings_notification_target(settings)


def _normalize_notification_target_argument(
    *,
    argument: str,
    message: Any,
) -> str | None:
    normalized = argument.strip()
    if not normalized:
        return None

    if normalized.casefold() == "me":
        sender_id = _sender_id(message)
        return None if sender_id is None else str(sender_id)

    if normalized.startswith("@") and len(normalized) > 1:
        return normalized

    if normalized.lstrip("-").isdigit():
        return str(int(normalized))

    return None


async def handle_action_mode_command(
    *,
    message: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
    bot: Any | None = None,
) -> ActionMode | None:
    if not await is_authorized_admin_sender(
        message=message,
        settings=settings,
        bot=bot,
    ):
        await _deny_admin_command(
            message=message,
            bot=bot,
            text="❌ Недостаточно прав для изменения режима модерации",
        )
        return None

    chat_id = _chat_id(message)
    argument = parse_action_mode_argument(getattr(message, "text", None))
    if argument is None:
        current_mode = await runtime_settings_repository.get_action_mode(
            default=settings.action_mode,
            chat_id=chat_id,
        )
        notification_target = await _current_notification_target(
            message=message,
            settings=settings,
            runtime_settings_repository=runtime_settings_repository,
            chat_id=chat_id,
        )
        await _send_admin_response(
            message=message,
            bot=bot,
            text=build_admin_panel_text(
                current_mode=current_mode,
                notification_target=notification_target,
            ),
            reply_markup=build_action_mode_keyboard(
                current_mode=current_mode,
                chat_id=chat_id,
            ),
        )
        return current_mode

    if argument in ACTION_MODE_RESET_ALIASES:
        await runtime_settings_repository.reset_action_mode(chat_id=chat_id)
        await _send_admin_response(
            message=message,
            bot=bot,
            text=f"✅ Runtime-режим сброшен. Активен режим из env: {settings.action_mode.value}",
            reply_markup=build_action_mode_keyboard(
                current_mode=settings.action_mode,
                chat_id=chat_id,
            ),
        )
        return settings.action_mode

    action_mode = ACTION_MODE_ALIASES.get(argument)
    if action_mode is None:
        await _send_admin_response(
            message=message,
            bot=bot,
            text="❌ Неверный режим. Доступно: delete или notify_admin",
        )
        return None

    await runtime_settings_repository.set_action_mode(action_mode, chat_id=chat_id)
    await _send_admin_response(
        message=message,
        bot=bot,
        text=f"✅ Режим модерации изменен: {action_mode.value}",
        reply_markup=build_action_mode_keyboard(
            current_mode=action_mode,
            chat_id=chat_id,
        ),
    )
    return action_mode


async def handle_admin_panel_command(
    *,
    message: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
    bot: Any | None = None,
) -> ActionMode | None:
    if not await is_authorized_admin_sender(
        message=message,
        settings=settings,
        bot=bot,
    ):
        await _deny_admin_command(
            message=message,
            bot=bot,
            text="❌ Недостаточно прав для панели администратора",
        )
        return None

    chat_id = _chat_id(message)
    current_mode = await runtime_settings_repository.get_action_mode(
        default=settings.action_mode,
        chat_id=chat_id,
    )
    notification_target = await _current_notification_target(
        message=message,
        settings=settings,
        runtime_settings_repository=runtime_settings_repository,
        chat_id=chat_id,
    )
    await _send_admin_response(
        message=message,
        bot=bot,
        text=build_admin_panel_text(
            current_mode=current_mode,
            notification_target=notification_target,
        ),
        reply_markup=build_action_mode_keyboard(
            current_mode=current_mode,
            chat_id=chat_id,
        ),
    )
    return current_mode


async def handle_notification_target_command(
    *,
    message: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
    bot: Any | None = None,
) -> str | None:
    if not await is_authorized_admin_sender(
        message=message,
        settings=settings,
        bot=bot,
    ):
        await _deny_admin_command(
            message=message,
            bot=bot,
            text="❌ Недостаточно прав для изменения уведомлений",
        )
        return None

    chat_id = _chat_id(message)
    if chat_id is None:
        await _send_admin_response(
            message=message,
            bot=bot,
            text="❌ Команда доступна только в чате",
        )
        return None

    argument = parse_notification_target_argument(getattr(message, "text", None))
    if argument is None:
        current_target = await _current_notification_target(
            message=message,
            settings=settings,
            runtime_settings_repository=runtime_settings_repository,
            chat_id=chat_id,
        )
        await _send_admin_response(
            message=message,
            bot=bot,
            text="Текущий получатель уведомлений: "
            f"{current_target or 'env/default'}\n\n"
            "Команды:\n"
            "/notify me\n"
            "/notify @username\n"
            "/notify 123456789\n"
            "/notify reset",
        )
        return current_target

    if argument.casefold() in NOTIFICATION_TARGET_RESET_ALIASES:
        await runtime_settings_repository.reset_notification_target(chat_id=chat_id)
        target = _settings_notification_target(settings)
        await _send_admin_response(
            message=message,
            bot=bot,
            text=f"✅ Получатель уведомлений сброшен: {target or 'env/default'}",
        )
        return target

    target = _normalize_notification_target_argument(
        argument=argument,
        message=message,
    )
    if target is None:
        await _send_admin_response(
            message=message,
            bot=bot,
            text="❌ Укажи @username, numeric user_id, me или reset",
        )
        return None

    await runtime_settings_repository.set_notification_target(
        chat_id=chat_id,
        target=target,
    )
    await _send_admin_response(
        message=message,
        bot=bot,
        text=f"✅ Получатель уведомлений изменен: {target}",
    )
    return target


async def handle_action_mode_callback(
    *,
    callback_query: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
    bot: Any | None = None,
) -> ActionMode | None:
    data = str(getattr(callback_query, "data", ""))
    argument, chat_id = parse_action_mode_callback_data(data)

    if not await is_authorized_admin_sender(
        message=callback_query,
        settings=settings,
        bot=bot,
        chat_id=chat_id,
    ):
        await callback_query.answer(
            text="❌ Недостаточно прав для изменения режима",
            show_alert=True,
        )
        return None

    if argument in ACTION_MODE_RESET_ALIASES:
        await runtime_settings_repository.reset_action_mode(chat_id=chat_id)
        current_mode = settings.action_mode
        answer_text = f"✅ Активен режим из env: {current_mode.value}"
    else:
        action_mode = ACTION_MODE_ALIASES.get(argument)
        if action_mode is None:
            await callback_query.answer(text="❌ Неверный режим", show_alert=True)
            return None

        await runtime_settings_repository.set_action_mode(action_mode, chat_id=chat_id)
        current_mode = action_mode
        answer_text = f"✅ Режим изменен: {current_mode.value}"

    message = getattr(callback_query, "message", None)
    if message is not None:
        try:
            notification_target = await _current_notification_target(
                message=callback_query,
                settings=settings,
                runtime_settings_repository=runtime_settings_repository,
                chat_id=chat_id,
            )
            await message.edit_text(
                build_admin_panel_text(
                    current_mode=current_mode,
                    notification_target=notification_target,
                ),
                reply_markup=build_action_mode_keyboard(
                    current_mode=current_mode,
                    chat_id=chat_id,
                ),
            )
        except Exception:
            pass
    await callback_query.answer(text=answer_text)
    return current_mode


@router.message(Command("mode"))
async def on_action_mode_command(
    message: Message,
    bot: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
) -> None:
    await handle_action_mode_command(
        message=message,
        settings=settings,
        runtime_settings_repository=runtime_settings_repository,
        bot=bot,
    )


@router.message(Command("admin"))
@router.message(Command("help"))
async def on_admin_panel_command(
    message: Message,
    bot: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
) -> None:
    await handle_admin_panel_command(
        message=message,
        settings=settings,
        runtime_settings_repository=runtime_settings_repository,
        bot=bot,
    )


@router.message(Command("notify"))
async def on_notification_target_command(
    message: Message,
    bot: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
) -> None:
    await handle_notification_target_command(
        message=message,
        settings=settings,
        runtime_settings_repository=runtime_settings_repository,
        bot=bot,
    )


@router.callback_query(F.data.startswith(f"{ACTION_MODE_CALLBACK_PREFIX}:"))
async def on_action_mode_callback(
    callback_query: CallbackQuery,
    bot: Any,
    settings: Settings,
    runtime_settings_repository: RuntimeSettingsRepository,
) -> None:
    await handle_action_mode_callback(
        callback_query=callback_query,
        settings=settings,
        runtime_settings_repository=runtime_settings_repository,
        bot=bot,
    )


__all__ = ("router",)
