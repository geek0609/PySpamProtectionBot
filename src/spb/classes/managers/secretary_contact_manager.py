"""Manager for the secretary_contacts table."""

import logging
import time
from datetime import timedelta

from spb.classes.cache import Cache, LoadFailedException
from spb.classes.database import Database
from spb.enums import SecretaryContactStatus
from spb.exceptions import DatabaseException
from spb.objects.database.secretary_contact import SecretaryContact

log = logging.getLogger(__name__)

CACHE_MAX_SIZE = 10_000
CACHE_EXPIRY = timedelta(minutes=10)


class SecretaryContactManager:
    """Manager for the secretary_contacts table: the people who wrote to a secretary-enabled
    business connection, and what the owner decided about them."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._cache: Cache[tuple[str, int], SecretaryContact] = Cache(CACHE_MAX_SIZE, CACHE_EXPIRY)

    def get_secretary_contact(
        self, business_connection_id: str, id: int
    ) -> SecretaryContact | None:
        """Returns a tracked contact of a business connection, or None when they are not
        tracked."""
        return self._cache.get((business_connection_id, id), self._load_secretary_contact, None)

    def register_secretary_contact(self, contact: SecretaryContact) -> None:
        """Records a contact the first time they are seen. A contact already tracked is left as
        it is."""
        inserted = self._database.execute(
            "INSERT OR IGNORE INTO secretary_contacts "
            "(business_connection_id, id, status, first_seen_at) VALUES (?, ?, ?, ?)",
            (
                contact.business_connection_id,
                contact.id,
                contact.status.name,
                contact.first_seen_at,
            ),
        )
        key = (contact.business_connection_id, contact.id)
        if inserted > 0:
            self._cache.put(key, contact)
        else:
            self._cache.remove(key)

    def set_secretary_contact_status(
        self, business_connection_id: str, id: int, status: SecretaryContactStatus
    ) -> None:
        """Sets a contact's status, starting to track them when they are not tracked yet, for
        example when the owner decides on somebody before they have written."""
        self._database.execute(
            "INSERT INTO secretary_contacts (business_connection_id, id, status, first_seen_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(business_connection_id, id) "
            "DO UPDATE SET status = excluded.status",
            (business_connection_id, id, status.name, int(time.time())),
        )
        self._cache.remove((business_connection_id, id))

    def delete_secretary_contacts(self, business_connection_id: str) -> None:
        """Removes every contact of a business connection, used when secretary mode is disabled."""
        self._database.execute(
            "DELETE FROM secretary_contacts WHERE business_connection_id = ?",
            (business_connection_id,),
        )
        self._cache.clear()

    def _load_secretary_contact(self, key: tuple[str, int]) -> SecretaryContact | None:
        business_connection_id, id = key
        try:
            return self._database.query_one(
                "SELECT * FROM secretary_contacts WHERE business_connection_id = ? AND id = ?",
                key,
                lambda row: SecretaryContact(
                    row["business_connection_id"],
                    row["id"],
                    SecretaryContactStatus.from_string(row["status"]),
                    row["first_seen_at"],
                ),
            )
        except DatabaseException as e:
            log.warning(
                "Failed to load secretary contact %s in connection %s: %s",
                id,
                business_connection_id,
                e,
            )
            raise LoadFailedException(f"Failed to load secretary contact {id}") from e
