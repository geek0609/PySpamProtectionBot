"""Persistent configuration of a user's secretary mode."""

from dataclasses import dataclass
from typing import Self

from spb.enums import ScanningBehavior


@dataclass(frozen=True, slots=True)
class SecretaryConfiguration:
    """Persistent configuration of a user who enabled the bot as their secretary.

    The user connected the bot through a Telegram Business connection. The record exists only while
    the business connection is enabled: starting secretary mode inserts a default configuration and
    disconnecting removes it. The business connection id lets a business message be routed back to
    the owning user.

    Attributes:
        user_id: The Telegram user id of the account the bot manages.
        business_connection_id: The Telegram business connection id the configuration belongs to.
        behavior: How messages from unknown contacts are handled.
        privacy_mode: Whether scanning minimizes optional Federation data.
        scanning_enabled: Whether first-contact message content is sent to Federation for
            scanning; when off, only the sender's Federation record is queried.
    """

    user_id: int
    business_connection_id: str
    behavior: ScanningBehavior
    privacy_mode: bool
    scanning_enabled: bool

    def __post_init__(self) -> None:
        if self.behavior not in (ScanningBehavior.PASSIVE, ScanningBehavior.STRICT):
            raise ValueError("Secretary Mode supports only Passive and Strict behavior")

    @classmethod
    def default(cls, user_id: int) -> Self:
        """Creates the configuration a user's secretary mode starts with.

        Content scanning starts disabled: a private message falsely flagged as spam could otherwise
        end up in a Federation report. The sender is still queried against Federation.
        """
        return cls(user_id, "", ScanningBehavior.STRICT, False, False)
