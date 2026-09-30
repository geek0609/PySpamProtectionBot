"""Manager for the operators table."""

import logging
import sqlite3
from datetime import timedelta
from types import MappingProxyType

from spb.classes.cache import Cache, LoadFailedException
from spb.classes.database import Database
from spb.exceptions import DatabaseException
from spb.objects.database.operator_identity import OperatorIdentity

log = logging.getLogger(__name__)

CACHE_MAX_SIZE = 10_000
CACHE_EXPIRY = timedelta(minutes=10)

OPERATOR_LIST_CACHE_EXPIRY = timedelta(seconds=60)
"""How long the full operator table snapshot may be reused by list_operators()."""

OPERATOR_LIST_KEY = "operators"
"""Fixed cache key holding the full operator table snapshot."""


class OperatorManager:
    """Manager for the operators table, which links Telegram users to operator identities.

    Hot lookups are backed by an in-memory cache so authorising every incoming message does not hit
    SQLite. Writes are applied to the database first and only then reflected in the cache, so a
    failed write can never leave a phantom cache entry.
    """

    def __init__(self, database: Database) -> None:
        self._database = database
        self._cache: Cache[int, OperatorIdentity] = Cache(CACHE_MAX_SIZE, CACHE_EXPIRY)
        self._list_cache: Cache[str, MappingProxyType[int, OperatorIdentity]] = Cache(
            1, OPERATOR_LIST_CACHE_EXPIRY
        )

    def get_operator(self, user_id: int) -> OperatorIdentity | None:
        """Returns the operator identity registered for a Telegram user id.

        A transient database read failure is logged and reported as absent for this lookup only;
        the next lookup retries the read.
        """
        return self._cache.get(user_id, self._load_operator, None)

    def list_operators(self) -> MappingProxyType[int, OperatorIdentity]:
        """Returns every Telegram user with a stored operator credential.

        The result is the full table snapshot, cached for a short time or until save_operator() or
        delete_operator() drops it. Because every write to the table flows through this manager,
        the background notification service observes new, changed or removed operators promptly
        without paying a full-table read on every poll cycle.

        Raises:
            DatabaseException: If the operator table cannot be read.
        """
        operators = self._list_cache.get(OPERATOR_LIST_KEY, self._load_all_operators)
        return operators if operators is not None else MappingProxyType({})

    def save_operator(self, user_id: int, identity: OperatorIdentity) -> None:
        """Stores or replaces the operator identity of a Telegram user id.

        Raises:
            DatabaseException: If the write fails.
        """
        self._database.execute(
            "INSERT INTO operators (id, access_token, operator_uuid) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET access_token = excluded.access_token, "
            "operator_uuid = excluded.operator_uuid",
            (user_id, identity.access_token, identity.operator_uuid),
        )
        self._cache.put(user_id, identity)
        self._list_cache.remove(OPERATOR_LIST_KEY)

    def delete_operator(self, user_id: int) -> None:
        """Removes the operator identity registered for a Telegram user id, if any.

        Raises:
            DatabaseException: If the deletion fails.
        """
        self._database.execute("DELETE FROM operators WHERE id = ?", (user_id,))
        self._cache.remove(user_id)
        self._list_cache.remove(OPERATOR_LIST_KEY)

    def _load_all_operators(self, key: str) -> MappingProxyType[int, OperatorIdentity]:
        rows = self._database.query(
            "SELECT * FROM operators ORDER BY id", (), lambda row: (int(row["id"]), _identity(row))
        )
        return MappingProxyType(dict(rows))

    def _load_operator(self, user_id: int) -> OperatorIdentity | None:
        try:
            return self._database.query_one(
                "SELECT * FROM operators WHERE id = ?", (user_id,), _identity
            )
        except DatabaseException as e:
            log.warning("Failed to load operator identity for user %s: %s", user_id, e)
            raise LoadFailedException(f"Failed to load operator {user_id}") from e


def _identity(row: sqlite3.Row) -> OperatorIdentity:
    return OperatorIdentity(row["operator_uuid"], row["access_token"])
