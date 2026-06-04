"""Domain models for moderation modes, actions and cleanup jobs"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ActionMode(str, Enum):
    DELETE = "delete"
    NOTIFY_ADMIN = "notify_admin"


class ModerationAction(str, Enum):
    NONE = "none"
    DELETE_MESSAGE = "delete_message"
    NOTIFY_ADMIN = "notify_admin"
    BAN_UNBAN = "ban_unban"
    WARN_USER = "warn_user"


@dataclass(frozen=True)
class AutoDeleteMessage:
    chat_id: int
    message_id: int
    delete_at: str
    user_id: int | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AutoDeleteMessage:
        user_id = data.get("user_id")
        return cls(
            chat_id=int(data["chat_id"]),
            message_id=int(data["message_id"]),
            delete_at=str(data["delete_at"]),
            user_id=None if user_id is None else int(user_id),
        )
