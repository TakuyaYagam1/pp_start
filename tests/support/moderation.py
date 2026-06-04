"""Shared fakes and factories for moderation tests"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from pydantic import SecretStr

from app.config import Settings
from app.domain import (
    ActionMode,
    DuplicateMessageState,
    LLMDecision,
    SpamDetectionResult,
    StopWordCheckResult,
)


@dataclass
class FakeBot:
    sent_messages: list[dict[str, object]] = field(default_factory=list)
    deleted_messages: list[dict[str, int]] = field(default_factory=list)
    bans: list[dict[str, int]] = field(default_factory=list)
    unbans: list[dict[str, int]] = field(default_factory=list)
    admin_user_ids: set[int] = field(default_factory=set)
    chat_member_calls: list[dict[str, int]] = field(default_factory=list)

    async def send_message(self, **kwargs: object) -> None:
        self.sent_messages.append(kwargs)

    async def get_chat_member(self, *, chat_id: int, user_id: int) -> SimpleNamespace:
        self.chat_member_calls.append({"chat_id": chat_id, "user_id": user_id})
        status = "administrator" if user_id in self.admin_user_ids else "member"
        return SimpleNamespace(status=status)

    async def delete_message(self, **kwargs: int) -> None:
        self.deleted_messages.append(kwargs)

    async def ban_chat_member(self, **kwargs: int) -> None:
        self.bans.append(kwargs)

    async def unban_chat_member(self, **kwargs: int) -> None:
        self.unbans.append(kwargs)


class AutoDeleteFakeBot(FakeBot):
    async def send_message(self, **kwargs: object) -> SimpleNamespace:
        self.sent_messages.append(kwargs)
        return SimpleNamespace(message_id=999)


class FailingSendMessageBot(FakeBot):
    async def send_message(self, **_kwargs: object) -> None:
        raise RuntimeError("send failed")


class FailingAutoDeleteMessageRepository:
    async def create(self, **_kwargs: object) -> None:
        raise RuntimeError("redis failed")


@dataclass
class FakeRuntimeSettingsRepository:
    action_mode: ActionMode
    notification_target: str | None = None

    async def get_action_mode(
        self,
        *,
        default: ActionMode,
        chat_id: int | None = None,
    ) -> ActionMode:
        return self.action_mode

    async def get_notification_target(self, *, chat_id: int) -> str | None:
        return self.notification_target


@dataclass
class FakeStopWordWarningRepository:
    mark_result: bool
    marked_term: str | None = None

    async def get_warned_term(self, *, chat_id: int, user_id: int) -> str | None:
        return self.marked_term

    async def mark_warned_once(
        self,
        *,
        chat_id: int,
        user_id: int,
        matched_term: str,
    ) -> bool:
        if self.mark_result:
            self.marked_term = matched_term
        return self.mark_result

    async def clear(self, *, chat_id: int, user_id: int) -> None:
        self.marked_term = None


class FakeSpamDetectorService:
    async def detect(self, message_text: str) -> SpamDetectionResult:
        return spam_result()


@dataclass
class FakeFloodSpamDetectorService:
    detect_calls: list[str] = field(default_factory=list)

    async def detect(self, message_text: str) -> SpamDetectionResult:
        self.detect_calls.append(message_text)
        return SpamDetectionResult(is_spam=False, reason="no_stop_word")


@dataclass
class FakeDuplicateMessageRepository:
    state: DuplicateMessageState
    warning_digest: str | None = None
    warning_grace_active: bool = False
    mark_warned_once_result: bool = True
    marked_warning_digest: str | None = None
    record_calls: list[dict[str, object]] = field(default_factory=list)
    cleared: bool = False
    warning_cleared: bool = False

    async def record_message(
        self,
        *,
        chat_id: int,
        user_id: int,
        message_id: int,
        content_key: str,
    ) -> DuplicateMessageState:
        self.record_calls.append(
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "message_id": message_id,
                "content_key": content_key,
            }
        )
        return self.state

    async def get_warning_digest(self, *, chat_id: int, user_id: int) -> str | None:
        return self.warning_digest

    async def mark_warned(
        self,
        *,
        chat_id: int,
        user_id: int,
        digest: str,
    ) -> None:
        self.marked_warning_digest = digest

    async def mark_warned_once(
        self,
        *,
        chat_id: int,
        user_id: int,
        digest: str,
    ) -> bool:
        if self.mark_warned_once_result:
            self.marked_warning_digest = digest
        return self.mark_warned_once_result

    async def has_warning_grace(self, *, chat_id: int, user_id: int) -> bool:
        return self.warning_grace_active

    async def clear(self, *, chat_id: int, user_id: int) -> None:
        self.cleared = True

    async def clear_warning(self, *, chat_id: int, user_id: int) -> None:
        self.warning_cleared = True
        self.marked_warning_digest = None


def make_settings(*, action_mode: str, log_file: Path) -> Settings:
    return Settings(
        bot_token=SecretStr("123456:test-token"),
        redis_url="redis://redis:6379/0",
        verify_timeout_seconds=180,
        action_mode=action_mode,
        admin_username="admin_user",
        admin_id=None,
        llm_api_key=SecretStr("llm-secret"),
        llm_base_url="https://llm.example/v1",
        llm_model="test-model",
        llm_timeout_seconds=5,
        log_level="INFO",
        log_file=str(log_file),
    )


def make_message(
    *,
    message_thread_id: int | None = None,
    message_id: int = 55,
    text: str | None = "казино прямо сейчас",
    sticker_unique_id: str | None = None,
    document_unique_id: str | None = None,
    document_file_name: str | None = None,
    document_mime_type: str | None = None,
    entities: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    sticker = (
        None
        if sticker_unique_id is None
        else SimpleNamespace(file_unique_id=sticker_unique_id)
    )
    document = (
        None
        if document_unique_id is None
        else SimpleNamespace(
            file_unique_id=document_unique_id,
            file_name=document_file_name,
            mime_type=document_mime_type,
        )
    )
    return SimpleNamespace(
        text=text,
        entities=entities,
        message_id=message_id,
        message_thread_id=message_thread_id,
        sticker=sticker,
        document=document,
        chat=SimpleNamespace(id=-100123, type="supergroup", username="public_group"),
        from_user=SimpleNamespace(id=42, is_bot=False, username="spammer"),
    )


def spam_result() -> SpamDetectionResult:
    return SpamDetectionResult(
        is_spam=True,
        reason="llm_spam",
        stop_word=StopWordCheckResult(matched=True, matched_term="казино"),
        llm_decision=LLMDecision.SPAM,
        matched_term="казино",
    )


def read_log(logger_name_log_file: Path) -> str:
    return logger_name_log_file.read_text(encoding="utf-8")
