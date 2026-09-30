"""Manager for the secretary_configuration table."""

import logging
import sqlite3
from datetime import timedelta

from spb.classes.cache import Cache, LoadFailedException
from spb.classes.database import Database
from spb.enums import ScanningBehavior
from spb.exceptions import DatabaseException
from spb.objects.database.secretary_configuration import SecretaryConfiguration

log = logging.getLogger(__name__)

CACHE_MAX_SIZE = 10_000
CACHE_EXPIRY = timedelta(minutes=10)

PENDING_CONNECTION = "business_connection_id IS NULL OR business_connection_id = ''"
"""Matches configurations still waiting for their business connection id."""


class SecretaryConfigurationManager:
    """Manager for the secretary_configuration table, one row per user in secretary mode.

    Configurations are looked up by user and, for every incoming business message, by business
    connection; both lookups are cached.
    """

    def __init__(self, database: Database) -> None:
        self._database = database
        self._cache: Cache[int, SecretaryConfiguration] = Cache(CACHE_MAX_SIZE, CACHE_EXPIRY)
        self._connection_cache: Cache[str, int] = Cache(CACHE_MAX_SIZE, CACHE_EXPIRY)

    def get_secretary_configuration(self, user_id: int) -> SecretaryConfiguration | None:
        """Returns a user's configuration, or None when they have none.

        A transient database read failure is logged and reported as absent for this lookup only.
        """
        return self._cache.get(user_id, self._load_secretary_configuration, None)

    def secretary_configuration_exists(self, user_id: int) -> bool:
        """Returns whether a user has a configuration."""
        return self.get_secretary_configuration(user_id) is not None

    def default_configuration(self, user_id: int) -> SecretaryConfiguration:
        """Returns the configuration a user without a row is treated as having."""
        return SecretaryConfiguration.default(user_id)

    def resolve(self, user_id: int) -> SecretaryConfiguration:
        """Returns a user's stored configuration, or the default when they have none."""
        return self.get_secretary_configuration(user_id) or self.default_configuration(user_id)

    def user_id_by_business_connection(self, business_connection_id: str) -> int | None:
        """Returns the user owning a business connection, when it is enabled in secretary mode.

        The reverse mapping is cached because it is resolved for every incoming business message.
        A cached mapping that no longer matches the owner's configuration is dropped, so writes
        never have to track the connection id they replace.
        """
        user_id = self._connection_cache.get(
            business_connection_id, self._load_user_id_by_business_connection, None
        )
        if user_id is not None:
            configuration = self.get_secretary_configuration(user_id)
            if (
                configuration is not None
                and configuration.business_connection_id == business_connection_id
            ):
                return user_id
        self._connection_cache.remove(business_connection_id)
        return None

    def create_secretary_configuration(self, user_id: int) -> None:
        """Creates a user's configuration with the schema's defaults, unless one already exists."""
        self._database.execute(
            "INSERT OR IGNORE INTO secretary_configuration (id) VALUES (?)", (user_id,)
        )
        self._cache.remove(user_id)

    def set_business_connection_id(self, user_id: int, business_connection_id: str) -> None:
        """Binds a business connection to a user, creating their configuration when they have
        none."""
        self._database.execute(
            "INSERT INTO secretary_configuration (id, business_connection_id) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "business_connection_id = excluded.business_connection_id",
            (user_id, business_connection_id),
        )
        self._cache.remove(user_id)
        self._connection_cache.put(business_connection_id, user_id)

    def set_behavior(self, user_id: int, behavior: ScanningBehavior) -> None:
        """Sets how messages from unknown contacts are handled.

        Raises:
            ValueError: If the behavior is not PASSIVE or STRICT, the only ones secretary mode
                supports.
        """
        if behavior not in (ScanningBehavior.PASSIVE, ScanningBehavior.STRICT):
            raise ValueError("Secretary Mode supports only Passive and Strict behavior")

        self._database.execute(
            "UPDATE secretary_configuration SET behavior = ? WHERE id = ?", (behavior.name, user_id)
        )
        self._cache.remove(user_id)

    def set_scanning_enabled(self, user_id: int, scanning_enabled: bool) -> None:
        """Sets whether first-contact message content is sent to Federation for scanning.

        The sender's Federation record is queried either way; this only controls whether the
        message itself leaves the conversation.
        """
        self._database.execute(
            "UPDATE secretary_configuration SET scanning_enabled = ? WHERE id = ?",
            (scanning_enabled, user_id),
        )
        self._cache.remove(user_id)

    def bind_unknown_connection(self, business_connection_id: str) -> int | None:
        """Binds a business connection to the only user whose configuration still awaits one.

        Business messages can arrive for connections whose business_connection update the bot
        never saw, for example while it was offline. Such a message cannot identify the owning user
        on its own, but the user who started secretary mode from the Manage Bot deep link has a
        configuration with an empty connection id. When exactly one such configuration exists, the
        connection is tied to that user so the message can be processed and later messages route
        normally.

        Returns:
            The newly bound user id, or None when there are no or several candidates.
        """
        try:
            candidates = self._database.query(
                f"SELECT id FROM secretary_configuration WHERE {PENDING_CONNECTION} LIMIT 2",
                (),
                lambda row: int(row["id"]),
            )
            if len(candidates) != 1:
                return None
            user_id = candidates[0]
            self.set_business_connection_id(user_id, business_connection_id)
        except DatabaseException as e:
            log.warning("Failed to bind business connection %s: %s", business_connection_id, e)
            return None

        log.info(
            "Bound business connection %s to the pending secretary configuration of user %s",
            business_connection_id,
            user_id,
        )
        return user_id

    def delete_secretary_configuration(self, user_id: int) -> None:
        """Removes a user's configuration, if any."""
        self._database.execute("DELETE FROM secretary_configuration WHERE id = ?", (user_id,))
        self._cache.remove(user_id)

    def _load_user_id_by_business_connection(self, business_connection_id: str) -> int | None:
        try:
            return self._database.query_one(
                "SELECT id FROM secretary_configuration WHERE business_connection_id = ?",
                (business_connection_id,),
                lambda row: int(row["id"]),
            )
        except DatabaseException as e:
            log.warning("Failed to resolve business connection %s: %s", business_connection_id, e)
            raise LoadFailedException(
                f"Failed to resolve business connection {business_connection_id}"
            ) from e

    def _load_secretary_configuration(self, user_id: int) -> SecretaryConfiguration | None:
        try:
            return self._database.query_one(
                "SELECT * FROM secretary_configuration WHERE id = ?", (user_id,), _configuration
            )
        except DatabaseException as e:
            log.warning("Failed to load secretary configuration for user %s: %s", user_id, e)
            raise LoadFailedException(f"Failed to load secretary configuration {user_id}") from e


def _configuration(row: sqlite3.Row) -> SecretaryConfiguration:
    behavior = ScanningBehavior.from_string(row["behavior"])
    return SecretaryConfiguration(
        row["id"],
        row["business_connection_id"],
        ScanningBehavior.PASSIVE if behavior is ScanningBehavior.MODERATE else behavior,
        False,
        bool(row["scanning_enabled"]),
    )
