"""Manager for the users table."""

import logging
import sqlite3
from datetime import timedelta

from spb.classes.cache import Cache, LoadFailedException
from spb.classes.database import Database
from spb.exceptions import DatabaseException
from spb.objects.database.user_identity import UserIdentity

log = logging.getLogger(__name__)

CACHE_MAX_SIZE = 100_000
CACHE_EXPIRY = timedelta(minutes=10)


class UserManager:
    """Manager for the users table.

    Users can be looked up either by Telegram user id or by username. The id cache stores the full
    UserIdentity; the username cache stores the mapping from username to user id. When a username
    changes, the old username entry is removed from the reverse cache before the new mapping is
    stored, so stale lookups resolve correctly.
    """

    def __init__(self, database: Database) -> None:
        self._database = database
        self._by_id: Cache[int, UserIdentity] = Cache(CACHE_MAX_SIZE, CACHE_EXPIRY)
        self._by_username: Cache[str, int] = Cache(CACHE_MAX_SIZE, CACHE_EXPIRY)

    def get_user(self, user_id: int) -> UserIdentity | None:
        """Returns the identity registered for a Telegram user id.

        Cache misses load the identity from the database. A transient database read failure is
        logged and reported as absent for this lookup only; the next lookup retries the read.
        """
        return self._by_id.get(user_id, self._load_user_by_id, None)

    def get_user_id_by_username(self, username: str | None) -> int | None:
        """Returns the Telegram user id associated with a username.

        Usernames are matched case-insensitively and without the leading '@'. A transient database
        read failure is logged and reported as absent for this lookup only.
        """
        normalized = _normalize_username(username)
        if normalized is None:
            return None
        return self._by_username.get(normalized, self._load_user_id_by_username, None)

    def user_exists(self, user_id: int) -> bool:
        """Returns whether an identity exists for a Telegram user id."""
        return self.get_user(user_id) is not None

    def save_user(self, identity: UserIdentity) -> None:
        """Stores or replaces the identity of a Telegram user.

        Writes are applied to the database first; the caches are only updated after the write
        succeeds. Telegram usernames can move between accounts, but the column is unique, so a
        username still recorded against another user is released from that user in the same
        transaction.

        Raises:
            DatabaseException: If the write fails.
        """
        username = _normalize_username(identity.username)

        previous = self.get_user(identity.user_id)
        previous_username = _normalize_username(previous.username) if previous else None
        if previous_username is not None and previous_username != username:
            self._by_username.remove(previous_username)

        def save(connection: sqlite3.Connection) -> list[int]:
            owners = []
            if username is not None:
                owners = [
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM users WHERE username = ? AND id != ?",
                        (username, identity.user_id),
                    )
                ]
                connection.execute(
                    "UPDATE users SET username = NULL WHERE username = ? AND id != ?",
                    (username, identity.user_id),
                )
            connection.execute(
                "INSERT INTO users (id, username, first_name, last_name) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET username = excluded.username, "
                "first_name = excluded.first_name, last_name = excluded.last_name",
                (identity.user_id, username, identity.first_name, identity.last_name),
            )
            return owners

        for owner in self._database.transaction(save):
            self._by_id.remove(owner)
        self._by_id.put(identity.user_id, identity)
        if username is not None:
            self._by_username.put(username, identity.user_id)

    def delete_user(self, user_id: int) -> None:
        """Removes the identity registered for a Telegram user id, if any.

        Raises:
            DatabaseException: If the deletion fails.
        """
        previous = self.get_user(user_id)
        self._database.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self._by_id.remove(user_id)

        previous_username = _normalize_username(previous.username) if previous else None
        if previous_username is not None:
            self._by_username.remove(previous_username)

    def _load_user_by_id(self, user_id: int) -> UserIdentity | None:
        try:
            return self._database.query_one(
                "SELECT * FROM users WHERE id = ?",
                (user_id,),
                lambda row: UserIdentity(
                    user_id, row["username"], row["first_name"], row["last_name"]
                ),
            )
        except DatabaseException as e:
            log.warning("Failed to load user identity for user %s: %s", user_id, e)
            raise LoadFailedException(f"Failed to load user {user_id}") from e

    def _load_user_id_by_username(self, username: str) -> int | None:
        try:
            return self._database.query_one(
                "SELECT id FROM users WHERE username = ?", (username,), lambda row: int(row["id"])
            )
        except DatabaseException as e:
            log.warning("Failed to load user id for username %s: %s", username, e)
            raise LoadFailedException(f"Failed to load username {username}") from e


def _normalize_username(username: str | None) -> str | None:
    """Strips a leading '@' and lowercases a username, None when nothing is left."""
    if username is None or not username.strip():
        return None

    normalized = username.strip().removeprefix("@").lower()
    return normalized or None
