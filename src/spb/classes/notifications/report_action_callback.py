"""A moderation button on a report notification."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Self

from spb.objects.admin_info import AdminInfo

PREFIX = "rpa"
"""Namespace of the moderation buttons."""


class Action(Enum):
    """The moderation actions offered on a report notification, with the administrator right each
    one needs. The same right is required of the moderator pressing the button and of the bot,
    which performs the action."""

    DELETE = ("delete", lambda admin: admin.can_delete_messages)
    MUTE = ("mute", lambda admin: admin.can_restrict_members)
    BAN = ("ban", lambda admin: admin.can_restrict_members)

    def __init__(self, code: str, permission: Callable[[AdminInfo], bool]) -> None:
        self.code = code
        self._permission = permission

    def permits(self, admin: AdminInfo | None) -> bool:
        """Returns whether the administrator, None when unknown, holds the right this action
        needs."""
        return admin is not None and self._permission(admin)

    @classmethod
    def from_code(cls, code: str) -> Self | None:
        """Resolves an action from its callback code, or None when the code is unknown."""
        return next((action for action in cls if action.code == code), None)


@dataclass(frozen=True, slots=True)
class ReportActionCallback:
    """A moderation button on a report notification: the reported message and the action to take.

    Everything the action needs travels in the callback data, as
    rpa:<chat_id>:<message_id>:<author_id>:<action>, so the buttons keep working for as long as
    the reported message exists and survive bot restarts. Even with the largest ids Telegram issues
    the data stays under Telegram's 64-byte callback data limit.

    Attributes:
        chat_id: The protected chat the reported message lives in.
        message_id: The reported message.
        author_id: The author of the reported message.
        action: The moderation action to take.
    """

    chat_id: int
    message_id: int
    author_id: int
    action: Action

    @property
    def data(self) -> str:
        """The callback data for this button."""
        return f"{PREFIX}:{self.chat_id}:{self.message_id}:{self.author_id}:{self.action.code}"

    @classmethod
    def parse(cls, data: str | None) -> Self | None:
        """Parses callback data produced by data, or returns None when it is not a moderation
        button."""
        if data is None:
            return None
        parts = data.split(":")
        if len(parts) != 5 or parts[0] != PREFIX:
            return None

        action = Action.from_code(parts[4])
        if action is None:
            return None
        try:
            return cls(int(parts[1]), int(parts[2]), int(parts[3]), action)
        except ValueError:
            return None
