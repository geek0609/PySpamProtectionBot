"""Cached snapshot of a chat administrator."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdminInfo:
    """Cached snapshot of a chat administrator's identity and permissions.

    Every administrator of a chat is cached, whatever their rights, so each caller checks the
    permission it needs: can_change_info for opening the settings menu, is_moderator for
    moderation, and the individual flags for per-moderator action buttons in report notifications,
    all without additional Telegram API calls.

    Attributes:
        id: The Telegram user id.
        is_owner: Whether the administrator is the chat owner.
        can_delete_messages: Whether the administrator can delete messages.
        can_restrict_members: Whether the administrator can restrict or ban members.
        can_change_info: Whether the administrator can change the chat's information.
    """

    id: int
    is_owner: bool
    can_delete_messages: bool
    can_restrict_members: bool
    can_change_info: bool

    @property
    def is_moderator(self) -> bool:
        """Whether this administrator can delete messages or restrict members; the owner can."""
        return self.is_owner or self.can_delete_messages or self.can_restrict_members
