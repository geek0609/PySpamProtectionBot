"""A snapshot of a Telegram user identity."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UserIdentity:
    """A lightweight snapshot of a Telegram user identity.

    Used by UserManager to map Telegram user ids to their current username and back again.

    Attributes:
        user_id: The Telegram user id.
        username: The username without the leading '@', or None when the user has none.
        first_name: The user's first name, or None.
        last_name: The user's last name, or None.
    """

    user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
