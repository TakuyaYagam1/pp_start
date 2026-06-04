"""Protocol ports that decouple usecases from infrastructure adapters"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain import (
    ActionMode,
    AutoDeleteMessage,
    DuplicateMessageState,
    PendingVerification,
)


class PendingVerificationStore(Protocol):
    async def create(
        self,
        *,
        user_id: int,
        chat_id: int,
        verification_message_id: int,
        message_thread_id: int | None = None,
        verification_chat_id: int | None = None,
    ) -> PendingVerification: ...

    async def get(
        self,
        *,
        chat_id: int,
        user_id: int,
    ) -> PendingVerification | None: ...

    async def delete(self, *, chat_id: int, user_id: int) -> bool: ...

    async def get_ttl(self, *, chat_id: int, user_id: int) -> int | None: ...


class VerifiedUserStore(Protocol):
    async def mark_verified(self, *, chat_id: int, user_id: int) -> None: ...

    async def is_verified(self, *, chat_id: int, user_id: int) -> bool: ...


class RuntimeSettingsStore(Protocol):
    async def get_action_mode(
        self,
        *,
        default: ActionMode,
        chat_id: int | None = None,
    ) -> ActionMode: ...

    async def set_action_mode(
        self,
        action_mode: ActionMode,
        *,
        chat_id: int | None = None,
    ) -> None: ...

    async def reset_action_mode(self, *, chat_id: int | None = None) -> None: ...

    async def get_notification_target(self, *, chat_id: int) -> str | None: ...

    async def set_notification_target(self, *, chat_id: int, target: str) -> None: ...

    async def reset_notification_target(self, *, chat_id: int) -> None: ...


class DuplicateMessageStore(Protocol):
    async def record_message(
        self,
        *,
        chat_id: int,
        user_id: int,
        message_id: int,
        content_key: str,
    ) -> DuplicateMessageState: ...

    async def get(
        self,
        *,
        chat_id: int,
        user_id: int,
    ) -> DuplicateMessageState | None: ...

    async def clear(self, *, chat_id: int, user_id: int) -> None: ...

    async def get_warning_digest(self, *, chat_id: int, user_id: int) -> str | None: ...

    async def mark_warned(
        self,
        *,
        chat_id: int,
        user_id: int,
        digest: str,
    ) -> None: ...

    async def mark_warned_once(
        self,
        *,
        chat_id: int,
        user_id: int,
        digest: str,
    ) -> bool: ...

    async def has_warning_grace(self, *, chat_id: int, user_id: int) -> bool: ...

    async def clear_warning(self, *, chat_id: int, user_id: int) -> None: ...


class StopWordWarningStore(Protocol):
    async def get_warned_term(self, *, chat_id: int, user_id: int) -> str | None: ...

    async def mark_warned_once(
        self,
        *,
        chat_id: int,
        user_id: int,
        matched_term: str,
    ) -> bool: ...

    async def clear(self, *, chat_id: int, user_id: int) -> None: ...


class AutoDeleteMessageStore(Protocol):
    async def create(
        self,
        *,
        chat_id: int,
        message_id: int,
        delete_at: datetime,
        user_id: int | None = None,
    ) -> AutoDeleteMessage: ...

    async def list(self) -> tuple[AutoDeleteMessage, ...]: ...

    async def delete(self, *, chat_id: int, message_id: int) -> bool: ...


class LLMSpamClient(Protocol):
    async def ask_is_spam(self, message_text: str) -> str: ...


class LLMResultCache(Protocol):
    async def get(self, text: str) -> str | None: ...

    async def set(self, text: str, result: str) -> None: ...
