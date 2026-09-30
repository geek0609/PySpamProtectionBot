from spb.classes.database import Database
from spb.classes.managers.operator_manager import OperatorManager
from spb.objects.database.operator_identity import OperatorIdentity
from tests.classes.managers.conftest import reopen


def test_stores_lists_and_removes(database: Database) -> None:
    operators = OperatorManager(database)
    operators.save_operator(42, OperatorIdentity("uuid-1", "token-1"))

    stored = operators.get_operator(42)
    assert stored is not None
    assert stored.access_token == "token-1"

    everyone = operators.list_operators()
    assert len(everyone) == 1
    assert everyone[42].operator_uuid == "uuid-1"

    operators.delete_operator(42)
    assert operators.get_operator(42) is None
    assert operators.list_operators() == {}


def test_replaces_credential(database: Database) -> None:
    operators = OperatorManager(database)
    operators.save_operator(42, OperatorIdentity("uuid", "old"))
    operators.save_operator(42, OperatorIdentity("uuid", "new"))

    reopened = reopen(database)
    stored = OperatorManager(reopened).get_operator(42)
    reopened.close()

    assert stored is not None
    assert stored.access_token == "new"


def test_unknown_operator_is_absent(database: Database) -> None:
    assert OperatorManager(database).get_operator(404) is None
