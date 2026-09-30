"""Persistent configuration of a protected chat."""

import sqlite3
from dataclasses import dataclass
from typing import Self

from spb.enums import JoinProtectionBehavior, ScanningBehavior


@dataclass(frozen=True, slots=True)
class ChatConfiguration:
    """Persistent configuration for a chat where the bot has administrator permissions.

    Attributes:
        chat_id: The Telegram chat id.
        enabled: Whether the bot's chat-specific features are enabled.
        scanning_enabled: Whether content scanning and author checks are active.
        scanning_behavior: How scanning results and users are handled.
        join_protection_enabled: Whether members are checked when they join.
        join_protection_behavior: How recommended member blocks are handled on joining.
        join_protection_notifications_enabled: Whether join-protection events notify moderators.
        privacy_mode: Whether scanning minimizes optional Federation data.
        reporting_enabled: Whether the /report command is enabled.
        moderator_notifications_enabled: Whether eligible moderators receive private notifications.
        scanning_notifications_enabled: Whether scanning events produce moderator notifications.
        reporting_notifications_enabled: Whether submitted reports produce moderator notifications.
        channel_link_id: The linked notification chat or channel id, or None.
        channel_link_verification_code: The one-time code used to link a notification chat, or None.
        channel_link_thread_id: The forum topic id in the linked notification chat, or None.
    """

    chat_id: int
    enabled: bool
    scanning_enabled: bool
    scanning_behavior: ScanningBehavior
    join_protection_enabled: bool
    join_protection_behavior: JoinProtectionBehavior
    join_protection_notifications_enabled: bool
    privacy_mode: bool
    reporting_enabled: bool
    moderator_notifications_enabled: bool
    scanning_notifications_enabled: bool
    reporting_notifications_enabled: bool
    channel_link_id: int | None
    channel_link_verification_code: int | None
    channel_link_thread_id: int | None

    @classmethod
    def default(cls, chat_id: int, privacy_mode: bool) -> Self:
        """Creates the configuration a chat starts with before anybody has changed anything.

        Protection is off until an owner turns it on; once on, it protects as strongly as it can,
        because a chat that asked for protection wants protection. These values mirror the column
        defaults in sql/chat_configuration.sql, which new rows are created with.

        Args:
            chat_id: The Telegram chat id.
            privacy_mode: Whether new chats minimise what they send to Federation, from
                bot.privacy_mode.
        """
        return cls(
            chat_id=chat_id,
            enabled=False,
            scanning_enabled=True,
            scanning_behavior=ScanningBehavior.STRICT,
            join_protection_enabled=True,
            join_protection_behavior=JoinProtectionBehavior.STRICT,
            join_protection_notifications_enabled=True,
            privacy_mode=privacy_mode,
            reporting_enabled=True,
            moderator_notifications_enabled=True,
            scanning_notifications_enabled=True,
            reporting_notifications_enabled=True,
            channel_link_id=None,
            channel_link_verification_code=None,
            channel_link_thread_id=None,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        """Reads a configuration out of its chat_configuration row."""
        scanning_behavior = ScanningBehavior.from_string(row["scanning_behavior"])
        scanning_notifications_enabled = row["scanning_notifications_enabled"] != 0

        # Passive scanning only notifies, so with notifications off it would do nothing at all. A
        # chat in that state is read back as moderate rather than silently unprotected.
        if scanning_behavior is ScanningBehavior.PASSIVE and not scanning_notifications_enabled:
            scanning_behavior = ScanningBehavior.MODERATE

        return cls(
            chat_id=row["chat_id"],
            enabled=row["enabled"] != 0,
            scanning_enabled=row["scanning_enabled"] != 0,
            scanning_behavior=scanning_behavior,
            join_protection_enabled=row["join_protection_enabled"] != 0,
            join_protection_behavior=JoinProtectionBehavior.from_string(
                row["join_protection_behavior"]
            ),
            join_protection_notifications_enabled=row["join_protection_notifications_enabled"] != 0,
            privacy_mode=row["privacy_mode"] != 0,
            reporting_enabled=row["reporting_enabled"] != 0,
            moderator_notifications_enabled=row["moderator_notifications_enabled"] != 0,
            scanning_notifications_enabled=scanning_notifications_enabled,
            reporting_notifications_enabled=row["reporting_notifications_enabled"] != 0,
            channel_link_id=row["channel_link_id"],
            channel_link_verification_code=row["channel_link_verification_code"],
            channel_link_thread_id=row["channel_link_thread_id"],
        )
