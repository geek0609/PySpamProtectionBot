from collections.abc import Iterator
from pathlib import Path

import pytest

from spb.classes.database import Database


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "database.db")
    yield database
    database.close()


def reopen(database: Database) -> Database:
    """Closes a database and opens it again, as a restart would."""
    database.close()
    return Database(database.path)
