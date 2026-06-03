from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionMode(str, Enum):
    DELETE = "delete"
    NOTIFY_ADMIN = "notify_admin"


class LLMDecision(str, Enum):
    SPAM = "spam"
    NOT_SPAM = "not_spam"
    UNKNOWN = "unknown"


class ModerationAction(str, Enum):
    NONE = "none"
    DELETE_MESSAGE = "delete_message"
    NOTIFY_ADMIN = "notify_admin"
    BAN_UNBAN = "ban_unban"
    WARN_USER = "warn_user"


@dataclass(frozen=True)
class PendingVerification:
    user_id: int
    chat_id: int
    verification_message_id: int
    created_at: str
    message_thread_id: int | None = None
    verification_chat_id: int | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> PendingVerification:
        return cls(
            user_id=int(data["user_id"]),
            chat_id=int(data["chat_id"]),
            verification_message_id=int(data["verification_message_id"]),
            created_at=str(data["created_at"]),
            message_thread_id=(
                None
                if data.get("message_thread_id") is None
                else int(data["message_thread_id"])
            ),
            verification_chat_id=(
                None
                if data.get("verification_chat_id") is None
                else int(data["verification_chat_id"])
            ),
        )


@dataclass(frozen=True)
class DuplicateMessageState:
    user_id: int
    chat_id: int
    digest: str
    content_key: str
    message_ids: tuple[int, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> DuplicateMessageState:
        return cls(
            user_id=int(data["user_id"]),
            chat_id=int(data["chat_id"]),
            digest=str(data["digest"]),
            content_key=str(data.get("content_key") or data["normalized_text"]),
            message_ids=tuple(int(message_id) for message_id in data["message_ids"]),
        )


@dataclass(frozen=True)
class StopWordCheckResult:
    matched: bool
    matched_term: str | None = None


@dataclass(frozen=True)
class SpamDetectionResult:
    is_spam: bool
    reason: str
    stop_word: StopWordCheckResult = field(
        default_factory=lambda: StopWordCheckResult(matched=False)
    )
    llm_decision: LLMDecision = LLMDecision.UNKNOWN
    moderation_action: ModerationAction = ModerationAction.NONE
    matched_term: str | None = None
