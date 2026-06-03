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
    handle_notification_target_command,
    is_admin_sender,
    is_authorized_admin_sender,
    parse_action_mode_argument,
    parse_action_mode_callback_data,
)


@dataclass
class FakeRuntimeSettingsRepository:
    action_mode: ActionMode | None = None
    notification_target: str | None = None
    reset_called: bool = False
    notification_reset_called: bool = False

    async def get_action_mode(
        self,
        *,
        default: ActionMode,
        chat_id: int | None = None,
    ) -> ActionMode:
        return self.action_mode or default

    async def set_action_mode(
        self,
        action_mode: ActionMode,
        *,
        chat_id: int | None = None,
    ) -> None:
        self.action_mode = action_mode

    async def reset_action_mode(self, *, chat_id: int | None = None) -> None:
        self.action_mode = None
        self.reset_called = True

    async def get_notification_target(self, *, chat_id: int) -> str | None:
        return self.notification_target

    async def set_notification_target(self, *, chat_id: int, target: str) -> None:
        self.notification_target = target

    async def reset_notification_target(self, *, chat_id: int) -> None:
        self.notification_target = None
        self.notification_reset_called = True


@dataclass
class FakeMessage:
    text: str
    from_user: SimpleNamespace
    chat: SimpleNamespace | None = None
    answers: list[str] = field(default_factory=list)
    reply_markups: list[Any] = field(default_factory=list)
    deleted: bool = False

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))

    async def delete(self) -> None:
        self.deleted = True


@dataclass
class FakeCallbackMessage:
    chat: SimpleNamespace | None = None
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


@dataclass
class FakeBot:
    admin_user_ids: set[int]
    sent_messages: list[dict[str, Any]] = field(default_factory=list)

    async def get_chat_member(self, *, chat_id: int, user_id: int) -> SimpleNamespace:
        status = "administrator" if user_id in self.admin_user_ids else "member"
        return SimpleNamespace(status=status)

    async def send_message(self, **kwargs: Any) -> None:
        self.sent_messages.append(kwargs)


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
        chat=SimpleNamespace(id=-100123, type="supergroup"),
    )


def test_parse_action_mode_argument_reads_first_command_argument() -> None:
    assert parse_action_mode_argument("/mode") is None
    assert parse_action_mode_argument("/mode delete") == "delete"
    assert parse_action_mode_argument("/mode@bot notify_admin now") == "notify_admin"


def test_parse_action_mode_callback_data_reads_origin_chat_id() -> None:
    assert parse_action_mode_callback_data("admin_mode:delete") == ("delete", None)
    assert parse_action_mode_callback_data("admin_mode:delete:-100123") == (
        "delete",
        -100123,
    )


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


def test_build_action_keyboard_can_keep_group_context_for_private_panel() -> None:
    keyboard = build_action_mode_keyboard(
        current_mode=ActionMode.DELETE,
        chat_id=-100123,
    )

    assert keyboard.inline_keyboard[0][0].callback_data == (
        f"{ACTION_MODE_CALLBACK_PREFIX}:delete:-100123"
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


def test_authorized_admin_sender_accepts_real_chat_admin_without_env_admin() -> None:
    async def run() -> None:
        message = _message(text="/mode delete", user_id=99, username="other")

        result = await is_authorized_admin_sender(
            message=message,
            settings=_settings(admin_id=None, admin_username=None),
            bot=FakeBot(admin_user_ids={99}),
        )

        assert result is True

    asyncio.run(run())


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


def test_action_mode_command_deletes_non_admin_group_command_when_bot_is_available() -> (
    None
):
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository()
        message = _message(text="/mode delete", user_id=99, username="other")
        bot = FakeBot(admin_user_ids=set())

        result = await handle_action_mode_command(
            message=message,
            settings=_settings(admin_id=None, admin_username=None),
            runtime_settings_repository=repository,
            bot=bot,
        )

        assert result is None
        assert message.deleted is True
        assert message.answers == []
        assert bot.sent_messages == []

    asyncio.run(run())


def test_notification_target_command_sets_sender_as_private_target() -> None:
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository()
        message = _message(text="/notify me", user_id=42, username="admin_user")

        result = await handle_notification_target_command(
            message=message,
            settings=_settings(),
            runtime_settings_repository=repository,
        )

        assert result == "42"
        assert repository.notification_target == "42"
        assert message.answers == ["✅ Получатель уведомлений изменен: 42"]

    asyncio.run(run())


def test_notification_target_command_resets_to_env_default() -> None:
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository(notification_target="@other_admin")
        message = _message(text="/notify reset")

        result = await handle_notification_target_command(
            message=message,
            settings=_settings(admin_id=None, admin_username="@admin_user"),
            runtime_settings_repository=repository,
        )

        assert result == "@admin_user"
        assert repository.notification_target is None
        assert repository.notification_reset_called is True
        assert message.answers == ["✅ Получатель уведомлений сброшен: @admin_user"]

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


def test_admin_panel_command_sends_group_panel_to_private_chat() -> None:
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository(action_mode=ActionMode.DELETE)
        message = _message(text="/admin", user_id=99, username="other")
        bot = FakeBot(admin_user_ids={99})

        result = await handle_admin_panel_command(
            message=message,
            settings=_settings(admin_id=None, admin_username=None),
            runtime_settings_repository=repository,
            bot=bot,
        )

        assert result == ActionMode.DELETE
        assert message.deleted is True
        assert message.answers == []
        assert bot.sent_messages[0]["chat_id"] == 99
        assert "Панель администратора" in bot.sent_messages[0]["text"]
        assert bot.sent_messages[0]["reply_markup"] is not None

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


def test_action_mode_callback_accepts_real_admin_from_private_panel_context() -> None:
    async def run() -> None:
        repository = FakeRuntimeSettingsRepository()
        callback_message = FakeCallbackMessage(
            chat=SimpleNamespace(id=99, type="private")
        )
        callback_query = FakeCallbackQuery(
            data=f"{ACTION_MODE_CALLBACK_PREFIX}:delete:-100123",
            from_user=SimpleNamespace(id=99, username="other"),
            message=callback_message,
        )

        result = await handle_action_mode_callback(
            callback_query=callback_query,
            settings=_settings(admin_id=None, admin_username=None),
            runtime_settings_repository=repository,
            bot=FakeBot(admin_user_ids={99}),
        )

        assert result == ActionMode.DELETE
        assert repository.action_mode == ActionMode.DELETE
        assert callback_query.answers == [{"text": "✅ Режим изменен: delete"}]
        assert callback_message.reply_markups[0].inline_keyboard[0][
            0
        ].callback_data == (f"{ACTION_MODE_CALLBACK_PREFIX}:delete:-100123")

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
