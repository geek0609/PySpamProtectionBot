from spb.classes.database import Database
from spb.classes.managers.user_manager import UserManager
from spb.objects.database.user_identity import UserIdentity
from tests.classes.managers.conftest import reopen


def test_stores_and_reads_users(database: Database) -> None:
    users = UserManager(database)
    users.save_user(UserIdentity(42, "tester", "Test", "User"))

    identity = users.get_user(42)
    assert identity is not None
    assert identity.username == "tester"
    assert identity.first_name == "Test"

    assert users.get_user_id_by_username("tester") == 42
    # Usernames match case-insensitively and with or without the @.
    assert users.get_user_id_by_username("@TESTER") == 42


def test_users_are_persistent(database: Database) -> None:
    UserManager(database).save_user(UserIdentity(42, "tester", "Test", None))

    reopened = reopen(database)

    assert UserManager(reopened).get_user(42) is not None
    reopened.close()


def test_renaming_invalidates_the_old_username(database: Database) -> None:
    users = UserManager(database)
    users.save_user(UserIdentity(42, "before", "Test", None))
    assert users.get_user_id_by_username("before") == 42

    users.save_user(UserIdentity(42, "after", "Test", None))

    assert users.get_user_id_by_username("after") == 42
    assert users.get_user_id_by_username("before") is None


def test_usernames_move_between_accounts(database: Database) -> None:
    users = UserManager(database)
    users.save_user(UserIdentity(42, "handle", "First", None))

    users.save_user(UserIdentity(43, "handle", "Second", None))

    assert users.get_user_id_by_username("handle") == 43
    previous_owner = users.get_user(42)
    assert previous_owner is not None
    assert previous_owner.username is None  # the previous owner releases the username

    reopened = reopen(database)
    stored = UserManager(reopened).get_user(42)
    assert stored is not None
    assert stored.username is None
    reopened.close()


def test_unknown_users_are_absent(database: Database) -> None:
    users = UserManager(database)

    assert users.get_user(404) is None
    assert not users.user_exists(404)


def test_deletes_users(database: Database) -> None:
    users = UserManager(database)
    users.save_user(UserIdentity(42, "tester", "Test", None))

    users.delete_user(42)

    assert not users.user_exists(42)
    assert users.get_user_id_by_username("tester") is None
