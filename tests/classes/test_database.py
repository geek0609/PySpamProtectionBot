import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from spb.classes.database import Database
from spb.exceptions import DatabaseException

EXPECTED_TABLES = {
    "chat_configuration",
    "language_preferences",
    "operators",
    "secretary_configuration",
    "secretary_contacts",
    "users",
}


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "database.db")
    yield database
    database.close()


def insert_user(database: Database, id: int, username: str) -> None:
    database.execute(
        "INSERT INTO users (id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
        (id, username, "First", "Last"),
    )


def usernames(database: Database) -> list[str]:
    return database.query("SELECT username FROM users ORDER BY id", (), lambda row: row[0])


def insert(connection: sqlite3.Connection, id: int, username: str) -> None:
    connection.execute("INSERT INTO users (id, username) VALUES (?, ?)", (id, username))


# Opening the database


def test_creates_database_and_schemas(tmp_path: Path) -> None:
    path = tmp_path / "database.db"
    database = Database(path)

    assert database.created
    assert path.exists()
    assert set(database.tables()) >= EXPECTED_TABLES
    database.close()


def test_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state" / "database.db"
    database = Database(path)

    assert path.exists()
    assert database.path == path.resolve()
    database.close()


def test_reopen_is_non_destructive(tmp_path: Path) -> None:
    path = tmp_path / "database.db"
    first = Database(path)
    insert_user(first, 1, "first")
    first.close()

    second = Database(path)

    assert not second.created
    assert second.exists("SELECT 1 FROM users WHERE id = ?", (1,))
    second.close()


def test_schemas_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "database.db"
    Database(path).close()

    second = Database(path)

    assert set(second.tables()) >= EXPECTED_TABLES
    second.close()


def test_resolves_relative_paths(database: Database) -> None:
    assert database.path.is_absolute()


# Running statements


def test_writes_and_reads(database: Database) -> None:
    insert_user(database, 7, "seven")

    username = database.query_one(
        "SELECT username FROM users WHERE id = ?", (7,), lambda row: row["username"]
    )

    assert username == "seven"


def test_empty_query_returns_nothing(database: Database) -> None:
    assert database.query_one("SELECT username FROM users WHERE id = ?", (404,), str) is None
    assert not database.exists("SELECT 1 FROM users WHERE id = ?", (404,))


def test_maps_every_row(database: Database) -> None:
    insert_user(database, 1, "alpha")
    insert_user(database, 2, "beta")
    insert_user(database, 3, "gamma")

    assert usernames(database) == ["alpha", "beta", "gamma"]


def test_reports_affected_rows(database: Database) -> None:
    insert_user(database, 1, "alpha")
    insert_user(database, 2, "beta")

    assert database.execute("UPDATE users SET first_name = ?", ("Renamed",)) == 2


def test_wraps_failures(database: Database) -> None:
    with pytest.raises(DatabaseException, match="no_such_table"):
        database.query("SELECT * FROM no_such_table", (), str)


# Running transactions


def test_commits_on_success(database: Database) -> None:
    def work(connection: sqlite3.Connection) -> int:
        insert(connection, 1, "alpha")
        insert(connection, 2, "beta")
        return 2

    assert database.transaction(work) == 2
    assert usernames(database) == ["alpha", "beta"]


def test_rolls_back_on_failure(database: Database) -> None:
    def work(connection: sqlite3.Connection) -> None:
        insert(connection, 1, "alpha")
        # A duplicate primary key: the first insert must not survive this.
        insert(connection, 1, "duplicate")

    with pytest.raises(DatabaseException):
        database.transaction(work)

    assert usernames(database) == []


def test_rolls_back_on_other_failure(database: Database) -> None:
    def work(connection: sqlite3.Connection) -> None:
        insert(connection, 1, "alpha")
        raise RuntimeError("handler changed its mind")

    with pytest.raises(RuntimeError):
        database.transaction(work)

    assert usernames(database) == []


# Closing the database


def test_closes_the_connection(tmp_path: Path) -> None:
    database = Database(tmp_path / "database.db")
    assert database.is_open

    database.close()

    assert not database.is_open


def test_close_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "database.db")

    database.close()
    database.close()

    assert not database.is_open


def test_refuses_work_after_close(tmp_path: Path) -> None:
    database = Database(tmp_path / "database.db")
    database.close()

    with pytest.raises(DatabaseException):
        database.query("SELECT 1", (), str)
