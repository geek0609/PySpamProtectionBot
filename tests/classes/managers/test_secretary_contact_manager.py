from spb.classes.database import Database
from spb.classes.managers.secretary_contact_manager import SecretaryContactManager
from spb.enums import SecretaryContactStatus
from spb.objects.database.secretary_contact import SecretaryContact
from tests.classes.managers.conftest import reopen


def status(contacts: SecretaryContactManager, connection: str, id: int) -> SecretaryContactStatus:
    contact = contacts.get_secretary_contact(connection, id)
    assert contact is not None
    return contact.status


def test_tracks_contact_status(database: Database) -> None:
    contacts = SecretaryContactManager(database)
    contacts.register_secretary_contact(
        SecretaryContact("conn-1", 99, SecretaryContactStatus.UNKNOWN, 1_700_000_000)
    )
    assert status(contacts, "conn-1", 99) is SecretaryContactStatus.UNKNOWN

    contacts.set_secretary_contact_status("conn-1", 99, SecretaryContactStatus.ALLOWED)
    assert status(contacts, "conn-1", 99) is SecretaryContactStatus.ALLOWED

    contacts.set_secretary_contact_status("conn-1", 99, SecretaryContactStatus.DENIED)
    reopened = reopen(database)
    assert status(SecretaryContactManager(reopened), "conn-1", 99) is SecretaryContactStatus.DENIED
    reopened.close()


def test_registering_again_keeps_the_decision(database: Database) -> None:
    contacts = SecretaryContactManager(database)
    contacts.set_secretary_contact_status("conn-1", 99, SecretaryContactStatus.ALLOWED)

    contacts.register_secretary_contact(
        SecretaryContact("conn-1", 99, SecretaryContactStatus.UNKNOWN, 1_700_000_000)
    )

    assert status(contacts, "conn-1", 99) is SecretaryContactStatus.ALLOWED


def test_decides_on_untracked_contacts(database: Database) -> None:
    SecretaryContactManager(database).set_secretary_contact_status(
        "conn-1", 99, SecretaryContactStatus.DENIED
    )

    reopened = reopen(database)
    assert status(SecretaryContactManager(reopened), "conn-1", 99) is SecretaryContactStatus.DENIED
    reopened.close()


def test_deletes_contacts_on_disconnect(database: Database) -> None:
    contacts = SecretaryContactManager(database)
    contacts.register_secretary_contact(
        SecretaryContact("conn-1", 99, SecretaryContactStatus.ALLOWED, 1)
    )
    contacts.register_secretary_contact(
        SecretaryContact("conn-2", 99, SecretaryContactStatus.ALLOWED, 1)
    )

    contacts.delete_secretary_contacts("conn-1")

    assert contacts.get_secretary_contact("conn-1", 99) is None
    # Other connections are untouched.
    assert contacts.get_secretary_contact("conn-2", 99) is not None
