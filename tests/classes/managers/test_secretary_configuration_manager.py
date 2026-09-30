import pytest

from spb.classes.database import Database
from spb.classes.managers.secretary_configuration_manager import SecretaryConfigurationManager
from spb.enums import ScanningBehavior
from tests.classes.managers.conftest import reopen


def test_stores_and_finds_by_connection(database: Database) -> None:
    SecretaryConfigurationManager(database).set_business_connection_id(42, "conn-1")

    reopened = reopen(database)
    secretary = SecretaryConfigurationManager(reopened)

    assert secretary.secretary_configuration_exists(42)
    assert secretary.user_id_by_business_connection("conn-1") == 42
    reopened.close()


def test_defaults_to_strict(database: Database) -> None:
    secretary = SecretaryConfigurationManager(database)
    assert secretary.default_configuration(42).behavior is ScanningBehavior.STRICT

    secretary.create_secretary_configuration(42)

    reopened = reopen(database)
    stored = SecretaryConfigurationManager(reopened).resolve(42)
    reopened.close()
    assert stored == secretary.default_configuration(42)


def test_scanning_starts_disabled_and_can_be_enabled(database: Database) -> None:
    secretary = SecretaryConfigurationManager(database)
    secretary.create_secretary_configuration(42)
    assert not secretary.resolve(42).scanning_enabled

    secretary.set_scanning_enabled(42, True)

    reopened = reopen(database)
    stored = SecretaryConfigurationManager(reopened).resolve(42)
    reopened.close()
    assert stored.scanning_enabled


def test_changes_behavior_and_connection(database: Database) -> None:
    secretary = SecretaryConfigurationManager(database)
    secretary.set_business_connection_id(42, "conn-1")
    secretary.set_behavior(42, ScanningBehavior.PASSIVE)
    assert secretary.user_id_by_business_connection("conn-1") == 42

    secretary.set_business_connection_id(42, "conn-2")

    stored = secretary.resolve(42)
    assert stored.behavior is ScanningBehavior.PASSIVE
    assert stored.business_connection_id == "conn-2"
    # The old connection is released.
    assert secretary.user_id_by_business_connection("conn-1") is None
    with pytest.raises(ValueError, match="Passive and Strict"):
        secretary.set_behavior(42, ScanningBehavior.MODERATE)


def test_binds_the_only_pending_configuration(database: Database) -> None:
    secretary = SecretaryConfigurationManager(database)
    secretary.create_secretary_configuration(42)

    assert secretary.bind_unknown_connection("conn-1") == 42
    assert secretary.user_id_by_business_connection("conn-1") == 42


def test_does_not_guess_between_pending_configurations(database: Database) -> None:
    secretary = SecretaryConfigurationManager(database)
    secretary.create_secretary_configuration(42)
    secretary.create_secretary_configuration(43)

    assert secretary.bind_unknown_connection("conn-1") is None
