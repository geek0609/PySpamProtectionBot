"""The SQLite access layer."""

import logging
import sqlite3
from collections.abc import Sequence
from importlib.resources import files
from os import PathLike
from pathlib import Path

from spb.classes.interfaces import RowMapper, TransactionWork
from spb.exceptions import DatabaseException

log = logging.getLogger(__name__)

SCHEMA_DIRECTORY = files("spb") / "resources" / "sql"
"""The directory the schemas are loaded from."""

BUSY_TIMEOUT_SECONDS = 5
"""How long SQLite waits for another process's write lock before failing a statement."""


class Database:
    """The SQLite access layer.

    This class owns access to the database and nothing else: opening the file, applying the
    schemas, and running statements safely. It deliberately knows nothing about users, chats or
    configurations; those live in the manager layer built on top of it, so how SQLite is reached
    and what is stored in it never mix.

    Every handler runs on the event loop's thread, so one connection serves them all and statements
    never contend with each other. WAL journal mode and a busy timeout still guard against another
    process holding the file.

    The constructor creates the database file and any missing parent directories, then applies
    every schema in sql/. close() must be the last thing shut down, so in-flight updates can still
    reach the database while they finish.
    """

    def __init__(self, path: str | PathLike[str]) -> None:
        """
        Args:
            path: The database file location.

        Raises:
            DatabaseException: If the file cannot be created or opened, or a schema cannot be
                applied.
        """
        self.path = Path(path).resolve()
        self.created = not self.path.exists()

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise DatabaseException(
                f"Failed to create database directory {self.path.parent}: {e}"
            ) from e

        try:
            # Autocommit mode: a plain statement commits on its own, and transaction() opens its
            # transactions explicitly.
            self._connection = sqlite3.connect(
                self.path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None
            )
            self._connection.row_factory = sqlite3.Row
            # WAL lets readers run beside a writer, NORMAL is the recommended durability level
            # under WAL, and the temp store keeps transient sorting in memory. Foreign keys are off
            # by default in SQLite and must be asked for.
            for pragma in (
                "journal_mode = WAL",
                "synchronous = NORMAL",
                "temp_store = MEMORY",
                "foreign_keys = ON",
            ):
                self._connection.execute(f"PRAGMA {pragma}")
        except sqlite3.Error as e:
            raise DatabaseException(f"Failed to open database {self.path}: {e}") from e

        self._open = True
        try:
            self._apply_schemas()
        except DatabaseException:
            self.close()
            raise

        log.info("%s SQLite database '%s'", "Created" if self.created else "Opened", self.path)

    @property
    def is_open(self) -> bool:
        """Whether the database is still usable."""
        return self._open

    def query[T](self, sql: str, params: Sequence[object], mapper: RowMapper[T]) -> list[T]:
        """Runs a query and maps every returned row, in result order.

        Raises:
            DatabaseException: If the query fails.
        """
        try:
            return [mapper(row) for row in self._connection.execute(sql, params)]
        except sqlite3.Error as e:
            raise DatabaseException(f"Query failed ({sql}): {e}") from e

    def query_one[T](self, sql: str, params: Sequence[object], mapper: RowMapper[T]) -> T | None:
        """Runs a query expected to match at most one row.

        Rows beyond the first are ignored, so callers do not have to add LIMIT 1 to lookups by
        primary key.

        Returns:
            The mapped row, or None when nothing matched.

        Raises:
            DatabaseException: If the query fails.
        """
        try:
            row = self._connection.execute(sql, params).fetchone()
        except sqlite3.Error as e:
            raise DatabaseException(f"Query failed ({sql}): {e}") from e

        return mapper(row) if row is not None else None

    def exists(self, sql: str, params: Sequence[object]) -> bool:
        """Returns whether the query matches at least one row.

        Raises:
            DatabaseException: If the query fails.
        """
        return self.query_one(sql, params, lambda row: True) is not None

    def execute(self, sql: str, params: Sequence[object] = ()) -> int:
        """Runs an insert, update or delete.

        Returns:
            The number of affected rows.

        Raises:
            DatabaseException: If the statement fails.
        """
        try:
            return self._connection.execute(sql, params).rowcount
        except sqlite3.Error as e:
            raise DatabaseException(f"Statement failed ({sql}): {e}") from e

    def transaction[T](self, work: TransactionWork[T]) -> T:
        """Runs several statements on the connection inside a single transaction.

        The transaction commits when the work returns and rolls back when it raises, so a
        multi-table write either lands completely or not at all. It takes the write lock when it
        begins: a deferred transaction that reads and then writes would have to upgrade its lock
        mid-flight, which under WAL fails at once if another writer committed meanwhile.

        Returns:
            Whatever the work returned.

        Raises:
            DatabaseException: If a statement or the commit failed, in which case the transaction
                was rolled back. Any other exception raised by the work also rolls the transaction
                back, but propagates as itself.
        """
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                result = work(self._connection)
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
            return result
        except sqlite3.Error as e:
            raise DatabaseException(f"Transaction failed: {e}") from e

    def tables(self) -> list[str]:
        """Returns the names of the tables in the database, in alphabetical order.

        Used to verify the database state at start-up and in tests; it is not meant for routine
        lookups.
        """
        return self.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name",
            (),
            lambda row: str(row[0]),
        )

    def close(self) -> None:
        """Closes the connection. Safe to call more than once."""
        if not self._open:
            return

        self._connection.close()
        self._open = False
        log.info("SQLite database '%s' closed", self.path)

    def _apply_schemas(self) -> None:
        """Applies every sql/*.sql schema, in file name order.

        Schemas must be idempotent (CREATE TABLE IF NOT EXISTS) so applying them to an existing
        database is a no-op, and independent of each other so file name order is enough; a schema
        that must run after another is given a numeric prefix.
        """
        schemas = sorted(
            (schema for schema in SCHEMA_DIRECTORY.iterdir() if schema.name.endswith(".sql")),
            key=lambda schema: schema.name,
        )

        try:
            for schema in schemas:
                self._connection.executescript(schema.read_text(encoding="utf-8"))
                log.debug("Applied schema %s", schema.name)
        except sqlite3.Error as e:
            raise DatabaseException(f"Failed to apply database schemas: {e}") from e
