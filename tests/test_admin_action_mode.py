from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from pydantic import SecretStr

from app.config import Settings
from app.core.models import ActionMode
from app.tg_bot.handlers.admin import (
    ACTION_MODE_CALLBACK_PREFIX,
    build_action_mode_keyboard,
    build_admin_panel_text,
    handle_action_mode_callback,
    handle_action_mode_command,
    handle_admin_panel_command,
    is_admin_sender,
    parse_action_mode_argument,
)


@dataclass
class FakeRuntimeSettingsRepository:
    action_mode: ActionMode | None = None
    reset_called: bool = False

    async def get_action_mode(self, *, default: ActionMode) -> ActionMode:
        return self.action_mode or default

    async def set_action_mode(self, action_mode: ActionMode) -> None:
        self.action_mode = action_mode

    async def reset_action_mode(self) -> None:
        self.action_mode = None
        self.reset_called = True


@dataclass
class FakeMessage:
    text: str
    from_user: SimpleNamespace
    answers: list[str] = field(default_factory=list)
    reply_markups: list[Any] = field(default_factory=list)

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


@dataclass
class FakeCallbackMessage:
    edited_texts: list[str] = field(default_factory=list)
    reply_markups: list[Any] = field(default_factory=list)

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edited_texts.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


@dataclass
class FakeCallbackQuery:
    data: str
    from_user: SimpleNamespace
    message: FakeCallbackMessage | None = None
    answers: list[dict[str, Any]] = field(default_factory=list)

    async def answer(self, **kwargs: Any) -> None:
        self.answers.append(kwargs)


def _settings(
    *,
    action_mode: str = "notify_admin",
    admin_username: str | None = "admin_user",
    admin_id: int | None = 42,
) -> Settings:
    return Settings(
        bot_token=SecretStr("123456:test-token"),
        redis_url="redis://redis:6379/0",
        verify_timeout_seconds=180,
        action_mode=action_mode,
        admin_username=admin_username,
        admin_id=admin_id,
        llm_api_key=SecretStr("llm-secret"),
        llm_base_url="https://llm.example/v1",
        llm_model="test-model",
        llm_timeout_seconds=5,
        log_level="INFO",
        log_file="spam.log",
    )


def _message(
    *,
    text: str,
    user_id: int = 42,
    username: str | None = "admin_user",
) -> FakeMessage:
    return FakeMessage(
        text=text,
        from_user=SimpleNamespace(id=user_id, username=username),
    )


def test_parse_action_mode_argument_reads_first_command_argument() -> None:
    assert parse_action_mode_argument("/mode") is None
    assert parse_action_mode_argument("/mode delete") == "delete"
    assert parse_action_mode_argument("/mode@bot notify_admin now") == "notify_admin"


def test_build_admin_panel_has_commands_and_action_buttons() -> None:
    text = build_admin_panel_text(current_mode=ActionMode.DELETE)
    keyboard = build_action_mode_keyboard(current_mode=ActionMode.DELETE)

    assert "/mode delete" in text
    assert "/mode notify_admin" in text
    assert keyboard.inline_keyboard[0][0].text == "✅ Удалять спам"
    assert keyboard.inline_keyboard[0][0].callback_data == (
        f"{ACTION_MODE_CALLBACK_PREFIX}:delete"
    )
    assert keyboard.inline_keyboard[0][1].callback_data == (
        f"{ACTION_MODE_CALLBACK_PREFIX}:notify_admin"
    )


def test_is_admin_sender_accepts_configured_id_or_username() -> None:
    settings = _settings(admin_id=7, admin_username="@Admin_User")

    assert is_admin_sender(
        message=_message(text="/mode", user_id=7, username=None),
        settings=settings,
    )
    assert is_admin_sender(
        message=_message(text="/mode", user_id=8, username="admin_user"),
        settings=settings,
    )
    assert not is_admin_sender(
        message=_message(text="/mode", user_id=8, username="other"),
        settings=settings,
    )


def test_action_mode_command_sets_runtime_mode_for_admin() -> None:
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository()
        message = _message(text="/mode delete")

        result = await handle_action_mode_command(
            message=message,
            settings=_settings(),
            runtime_settings_repository=repository,
        )

        assert result == ActionMode.DELETE
        assert repository.action_mode == ActionMode.DELETE
        assert message.answers == ["✅ Режим модерации изменен: delete"]
        assert message.reply_markups[-1] is not None

    asyncio.run(run())


def test_action_mode_command_shows_current_runtime_mode() -> None:
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository(action_mode=ActionMode.DELETE)
        message = _message(text="/mode")

        result = await handle_action_mode_command(
            message=message,
            settings=_settings(),
            runtime_settings_repository=repository,
        )

        assert result == ActionMode.DELETE
        assert "delete" in message.answers[0]
        assert message.reply_markups[0] is not None

    asyncio.run(run())


def test_action_mode_command_resets_to_env_default() -> None:
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository(action_mode=ActionMode.DELETE)
        message = _message(text="/mode reset")

        result = await handle_action_mode_command(
            message=message,
            settings=_settings(action_mode="notify_admin"),
            runtime_settings_repository=repository,
        )

        assert result == ActionMode.NOTIFY_ADMIN
        assert repository.action_mode is None
        assert repository.reset_called is True
        assert "notify_admin" in message.answers[0]
        assert message.answers[0].startswith("✅")

    asyncio.run(run())


def test_action_mode_command_rejects_non_admin_sender() -> None:
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository()
        message = _message(text="/mode delete", user_id=99, username="other")

        result = await handle_action_mode_command(
            message=message,
            settings=_settings(),
            runtime_settings_repository=repository,
        )

        assert result is None
        assert repository.action_mode is None
        assert message.answers == [
            "❌ Недостаточно прав для изменения режима модерации"
        ]

    asyncio.run(run())


def test_admin_panel_command_shows_keyboard_for_admin() -> None:
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository(action_mode=ActionMode.DELETE)
        message = _message(text="/admin")

        result = await handle_admin_panel_command(
            message=message,
            settings=_settings(),
            runtime_settings_repository=repository,
        )

        assert result == ActionMode.DELETE
        assert "Панель администратора" in message.answers[0]
        assert message.reply_markups[0] is not None

    asyncio.run(run())


def test_action_mode_callback_sets_mode_and_edits_admin_panel() -> None:
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository()
        callback_message = FakeCallbackMessage()
        callback_query = FakeCallbackQuery(
            data=f"{ACTION_MODE_CALLBACK_PREFIX}:delete",
            from_user=SimpleNamespace(id=42, username="admin_user"),
            message=callback_message,
        )

        result = await handle_action_mode_callback(
            callback_query=callback_query,
            settings=_settings(),
            runtime_settings_repository=repository,
        )

        assert result == ActionMode.DELETE
        assert repository.action_mode == ActionMode.DELETE
        assert callback_query.answers == [{"text": "✅ Режим изменен: delete"}]
        assert "delete" in callback_message.edited_texts[0]
        assert callback_message.reply_markups[0] is not None

    asyncio.run(run())


def test_action_mode_callback_rejects_non_admin_sender() -> None:
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository()
        callback_query = FakeCallbackQuery(
            data=f"{ACTION_MODE_CALLBACK_PREFIX}:delete",
            from_user=SimpleNamespace(id=99, username="other"),
            message=FakeCallbackMessage(),
        )

        result = await handle_action_mode_callback(
            callback_query=callback_query,
            settings=_settings(),
            runtime_settings_repository=repository,
        )

        assert result is None
        assert repository.action_mode is None
        assert callback_query.answers == [
            {
                "text": "❌ Недостаточно прав для изменения режима",
                "show_alert": True,
            }
        ]

    asyncio.run(run())
