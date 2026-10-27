"""Manager for the chat_configuration table."""

import logging
import sqlite3
from datetime import timedelta
from enum import Enum

from spb.classes.cache import Cache, LoadFailedException
from spb.classes.database import Database
from spb.enums import JoinProtectionBehavior, ScanningBehavior
from spb.exceptions import DatabaseException
from spb.objects.database.chat_configuration import ChatConfiguration

log = logging.getLogger(__name__)

CACHE_MAX_SIZE = 10_000
CACHE_EXPIRY = timedelta(minutes=5)


class ChatConfigurationManager:
    """Manager for the chat_configuration table.

    A row exists only while protection is enabled in a chat: enable_chat_configuration() creates it
    with the schema's defaults and delete_chat_configuration() removes it. Every setting is changed
    individually by chat id, so a write only ever touches the columns it means to. Hot lookups are
    backed by an in-memory cache, so checking whether the bot is enabled in a chat does not hit
    SQLite on every message.
    """

    def __init__(self, database: Database, default_privacy_mode: bool) -> None:
        """
        Args:
            database: The database holding the chat_configuration table.
            default_privacy_mode: The privacy mode new chats start with, from bot.privacy_mode.
        """
        self._database = database
        self._default_privacy_mode = default_privacy_mode
        self._migrate_schema()
        self._cache: Cache[int, ChatConfiguration] = Cache(CACHE_MAX_SIZE, CACHE_EXPIRY)
        self._channel_link_cache: Cache[int, int] = Cache(CACHE_MAX_SIZE, CACHE_EXPIRY)
        self._verification_code_cache: Cache[int, int] = Cache(CACHE_MAX_SIZE, CACHE_EXPIRY)

    def get_chat_configuration(self, chat_id: int) -> ChatConfiguration | None:
        """Returns the configuration of a chat, or None when it has none.

        A transient database read failure is logged and reported as absent for this lookup only;
        the next lookup retries the read.
        """
        return self._cache.get(chat_id, self._load_chat_configuration, None)

    def chat_configuration_exists(self, chat_id: int) -> bool:
        """Returns whether a configuration exists for a chat."""
        return self.get_chat_configuration(chat_id) is not None

    def default_configuration(self, chat_id: int) -> ChatConfiguration:
        """Returns the disabled configuration a chat without a row is treated as having."""
        return ChatConfiguration.default(chat_id, self._default_privacy_mode)

    def resolve(self, chat_id: int) -> ChatConfiguration:
        """Returns the stored configuration of a chat, or the default when it has none."""
        return self.get_chat_configuration(chat_id) or self.default_configuration(chat_id)

    def enable_chat_configuration(self, chat_id: int) -> None:
        """Enables protection in a chat, creating its row with the schema's defaults and this bot's
        privacy-mode default when it has none.

        Raises:
            DatabaseException: If the write fails.
        """
        self._database.execute(
            "INSERT INTO chat_configuration (chat_id, enabled, privacy_mode) VALUES (?, 1, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET enabled = 1",
            (chat_id, int(self._default_privacy_mode)),
        )
        self._cache.remove(chat_id)

    def delete_chat_configuration(self, chat_id: int) -> None:
        """Removes the configuration of a chat, if any."""
        self._database.execute("DELETE FROM chat_configuration WHERE chat_id = ?", (chat_id,))
        self._cache.remove(chat_id)

    def migrate_chat(self, from_chat_id: int, to_chat_id: int) -> None:
        """Moves a chat's configuration to a new chat id, for a group Telegram upgraded to a
        supergroup. Nothing moves when the old chat has no configuration or the new chat already
        has one."""
        self._database.execute(
            "UPDATE chat_configuration SET chat_id = ? WHERE chat_id = ? "
            "AND NOT EXISTS (SELECT 1 FROM chat_configuration WHERE chat_id = ?)",
            (to_chat_id, from_chat_id, to_chat_id),
        )
        self._cache.remove(from_chat_id)
        self._cache.remove(to_chat_id)
        # The reverse lookups map to chat ids; a mapping to the old id would be dropped as stale
        # on its next use and miss once, so they are cleared outright for this rare event.
        self._channel_link_cache.clear()
        self._verification_code_cache.clear()

    def set_scanning_enabled(self, chat_id: int, enabled: bool) -> None:
        self._update(chat_id, "scanning_enabled = ?", enabled)

    def set_block_ads_trackers(self, chat_id: int, enabled: bool) -> None:
        self._update(chat_id, "block_ads_trackers = ?", enabled)

    def set_scanning_behavior(self, chat_id: int, behavior: ScanningBehavior) -> None:
        self._update(chat_id, "scanning_behavior = ?", behavior)

    def set_scanning_notifications_enabled(self, chat_id: int, enabled: bool) -> None:
        self._update(chat_id, "scanning_notifications_enabled = ?", enabled)

    def set_join_protection_enabled(self, chat_id: int, enabled: bool) -> None:
        self._update(chat_id, "join_protection_enabled = ?", enabled)

    def set_join_protection_behavior(self, chat_id: int, behavior: JoinProtectionBehavior) -> None:
        self._update(chat_id, "join_protection_behavior = ?", behavior)

    def set_join_protection_notifications_enabled(self, chat_id: int, enabled: bool) -> None:
        self._update(chat_id, "join_protection_notifications_enabled = ?", enabled)

    def set_privacy_mode(self, chat_id: int, privacy_mode: bool) -> None:
        self._update(chat_id, "privacy_mode = ?", privacy_mode)

    def set_reporting_enabled(self, chat_id: int, enabled: bool) -> None:
        self._update(chat_id, "reporting_enabled = ?", enabled)

    def set_reporting_notifications_enabled(self, chat_id: int, enabled: bool) -> None:
        self._update(chat_id, "reporting_notifications_enabled = ?", enabled)

    def set_moderator_notifications_enabled(self, chat_id: int, enabled: bool) -> None:
        self._update(chat_id, "moderator_notifications_enabled = ?", enabled)

    def set_channel_link_verification_code(
        self, chat_id: int, verification_code: int | None
    ) -> None:
        """Sets or clears the code a notification chat sends with /connect to link itself."""
        self._update(chat_id, "channel_link_verification_code = ?", verification_code)
        if verification_code is not None:
            self._verification_code_cache.put(verification_code, chat_id)

    def link_channel(
        self, chat_id: int, channel_link_id: int, channel_link_thread_id: int | None
    ) -> None:
        """Links a notification chat, consuming the verification code that authorised it.

        Args:
            chat_id: The protected chat.
            channel_link_id: The notification chat being linked.
            channel_link_thread_id: The forum topic notifications are posted in, or None.
        """
        self._update(
            chat_id,
            "channel_link_id = ?, channel_link_thread_id = ?, "
            "channel_link_verification_code = NULL",
            channel_link_id,
            channel_link_thread_id,
        )
        self._channel_link_cache.put(channel_link_id, chat_id)

    def unlink_channel(self, chat_id: int) -> None:
        """Unlinks the notification chat and discards any pending verification code."""
        self._update(chat_id, "channel_link_id = NULL, channel_link_verification_code = NULL")

    def get_chat_configuration_by_channel_link_id(
        self, channel_link_id: int
    ) -> ChatConfiguration | None:
        """Returns the configuration whose linked notification chat is the given one.

        The reverse mapping from channel link id to chat id is cached because this lookup cannot use
        the primary chat-id cache; the configuration itself still comes from the primary cache. A
        cached mapping that no longer matches the configuration is dropped, so writes never have to
        track the value they replace.
        """
        chat_id = self._channel_link_cache.get(
            channel_link_id, lambda key: self._load_chat_id("channel_link_id", key), None
        )
        configuration = self.get_chat_configuration(chat_id) if chat_id is not None else None
        if configuration is not None and configuration.channel_link_id == channel_link_id:
            return configuration
        self._channel_link_cache.remove(channel_link_id)
        return None

    def get_chat_configuration_by_channel_link_verification_code(
        self, verification_code: int
    ) -> ChatConfiguration | None:
        """Returns the configuration whose notification-chat verification code is the given one,
        verified against the configuration the same way as the channel link lookup."""
        chat_id = self._verification_code_cache.get(
            verification_code,
            lambda key: self._load_chat_id("channel_link_verification_code", key),
            None,
        )
        configuration = self.get_chat_configuration(chat_id) if chat_id is not None else None
        if (
            configuration is not None
            and configuration.channel_link_verification_code == verification_code
        ):
            return configuration
        self._verification_code_cache.remove(verification_code)
        return None

    def _migrate_schema(self) -> None:
        """Adds the columns of later releases to a chat_configuration table created before them.

        The schema file only creates missing tables, so a database from an earlier release keeps
        its old columns until they are added here. block_ads_trackers is added enabled, so existing
        chats block ads and trackers like new ones.

        Raises:
            DatabaseException: If the table could not be migrated.
        """

        def migrate(connection: sqlite3.Connection) -> None:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(chat_configuration)")
            }
            if "block_ads_trackers" not in columns:
                connection.execute(
                    "ALTER TABLE chat_configuration "
                    "ADD COLUMN block_ads_trackers INTEGER NOT NULL DEFAULT 1"
                )
                log.info("Added missing column block_ads_trackers to chat_configuration")

        self._database.transaction(migrate)

    def _update(self, chat_id: int, assignments: str, *values: object) -> None:
        """Applies column assignments to one chat's row and drops its cached configuration.

        Booleans are stored as 0 or 1 and enumerations by name.
        """
        stored = [
            value.name
            if isinstance(value, Enum)
            else int(value)
            if isinstance(value, bool)
            else value
            for value in values
        ]
        self._database.execute(
            f"UPDATE chat_configuration SET {assignments} WHERE chat_id = ?", (*stored, chat_id)
        )
        self._cache.remove(chat_id)

    def _load_chat_id(self, column: str, value: int) -> int | None:
        try:
            return self._database.query_one(
                f"SELECT chat_id FROM chat_configuration WHERE {column} = ?",
                (value,),
                lambda row: int(row["chat_id"]),
            )
        except DatabaseException as e:
            log.warning("Failed to load chat configuration by %s %s: %s", column, value, e)
            raise LoadFailedException(f"Failed to load chat configuration by {column}") from e

    def _load_chat_configuration(self, chat_id: int) -> ChatConfiguration | None:
        try:
            return self._database.query_one(
                "SELECT * FROM chat_configuration WHERE chat_id = ?",
                (chat_id,),
                ChatConfiguration.from_row,
            )
        except DatabaseException as e:
            log.warning("Failed to load chat configuration for chat %s: %s", chat_id, e)
            raise LoadFailedException(f"Failed to load chat configuration {chat_id}") from e
