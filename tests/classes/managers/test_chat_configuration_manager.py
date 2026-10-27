import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from spb.classes.database import Database
from spb.classes.managers.chat_configuration_manager import ChatConfigurationManager
from spb.enums import JoinProtectionBehavior, ScanningBehavior
from tests.classes.managers.conftest import reopen


def test_defaults_are_not_stored(database: Database) -> None:
    chats = ChatConfigurationManager(database, False)

    defaults = chats.resolve(-100)

    assert not defaults.enabled  # a chat is off until somebody turns it on
    assert defaults.scanning_enabled
    # A chat that turns protection on gets the strongest protection by default.
    assert defaults.scanning_behavior is ScanningBehavior.STRICT
    assert defaults.join_protection_behavior is JoinProtectionBehavior.STRICT
    assert not chats.chat_configuration_exists(-100)


def test_enabled_chat_takes_schema_defaults(database: Database) -> None:
    # New rows take their values from the schema's column defaults; the code's defaults describe
    # chats without a row. The two must never drift apart.
    ChatConfigurationManager(database, False).enable_chat_configuration(-100)

    reopened = reopen(database)
    chats = ChatConfigurationManager(reopened, False)
    stored = chats.resolve(-100)
    reopened.close()

    assert stored == replace(chats.default_configuration(-100), enabled=True)


def test_setters_change_one_column(database: Database) -> None:
    chats = ChatConfigurationManager(database, False)
    chats.enable_chat_configuration(-100)
    before = chats.resolve(-100)

    chats.set_scanning_behavior(-100, ScanningBehavior.MODERATE)
    chats.set_join_protection_behavior(-100, JoinProtectionBehavior.PASSIVE)
    chats.set_privacy_mode(-100, True)
    chats.set_reporting_enabled(-100, False)

    reopened = reopen(database)
    after = ChatConfigurationManager(reopened, False).resolve(-100)
    reopened.close()

    assert after.scanning_behavior is ScanningBehavior.MODERATE
    assert after.join_protection_behavior is JoinProtectionBehavior.PASSIVE
    assert after.privacy_mode
    assert not after.reporting_enabled
    assert after.scanning_enabled == before.scanning_enabled
    assert after.scanning_notifications_enabled == before.scanning_notifications_enabled
    assert after.moderator_notifications_enabled == before.moderator_notifications_enabled


def test_setters_do_not_create_rows(database: Database) -> None:
    chats = ChatConfigurationManager(database, False)

    chats.set_scanning_enabled(-100, False)

    assert not chats.chat_configuration_exists(-100)


def test_finds_by_channel_link(database: Database) -> None:
    chats = ChatConfigurationManager(database, False)
    chats.enable_chat_configuration(-100)
    chats.set_channel_link_verification_code(-100, 1234)
    by_code = chats.get_chat_configuration_by_channel_link_verification_code(1234)
    assert by_code is not None
    assert by_code.chat_id == -100

    chats.link_channel(-100, -200, 7)

    linked = chats.get_chat_configuration_by_channel_link_id(-200)
    assert linked is not None
    assert linked.chat_id == -100
    assert linked.channel_link_thread_id == 7
    assert linked.channel_link_verification_code is None
    # A used code no longer resolves.
    assert chats.get_chat_configuration_by_channel_link_verification_code(1234) is None


def test_stale_channel_links_do_not_resolve(database: Database) -> None:
    chats = ChatConfigurationManager(database, False)
    chats.enable_chat_configuration(-100)
    chats.link_channel(-100, -200, None)
    assert chats.get_chat_configuration_by_channel_link_id(-200) is not None

    chats.link_channel(-100, -300, None)
    assert chats.get_chat_configuration_by_channel_link_id(-200) is None
    assert chats.get_chat_configuration_by_channel_link_id(-300) is not None

    chats.unlink_channel(-100)
    assert chats.get_chat_configuration_by_channel_link_id(-300) is None


def test_applies_privacy_mode_default(database: Database) -> None:
    assert ChatConfigurationManager(database, True).default_configuration(-1).privacy_mode
    assert not ChatConfigurationManager(database, False).default_configuration(-1).privacy_mode


def test_behaviour_levels_share_one_order() -> None:
    # The settings menu lists levels in declaration order, so two enumerations declared in
    # different orders would put the same buttons in different places on two pages.
    assert [level.name for level in ScanningBehavior] == ["PASSIVE", "MODERATE", "STRICT"]
    assert [level.name for level in JoinProtectionBehavior] == ["PASSIVE", "MODERATE", "STRICT"]


def test_deletes_configuration(database: Database) -> None:
    chats = ChatConfigurationManager(database, False)
    chats.enable_chat_configuration(-100)

    chats.delete_chat_configuration(-100)

    assert not chats.resolve(-100).enabled


def test_migrates_a_chat(database: Database) -> None:
    chats = ChatConfigurationManager(database, False)
    chats.enable_chat_configuration(-100)

    chats.migrate_chat(-100, -1000000000100)

    assert not chats.chat_configuration_exists(-100)
    assert chats.resolve(-1000000000100).enabled


def test_blocking_ads_and_trackers_is_on_by_default_and_can_be_turned_off(
    database: Database,
) -> None:
    chats = ChatConfigurationManager(database, False)
    chats.enable_chat_configuration(-100)
    assert chats.resolve(-100).block_ads_trackers

    chats.set_block_ads_trackers(-100, False)

    reopened = reopen(database)
    assert not ChatConfigurationManager(reopened, False).resolve(-100).block_ads_trackers
    reopened.close()


def test_a_table_from_an_earlier_release_gains_block_ads_trackers(tmp_path: Path) -> None:
    path = tmp_path / "earlier.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        # The table as releases before block_ads_trackers created it.
        connection.execute(
            """
            CREATE TABLE chat_configuration
            (
                chat_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                scanning_enabled INTEGER NOT NULL DEFAULT 1,
                scanning_behavior TEXT NOT NULL DEFAULT 'STRICT',
                join_protection_enabled INTEGER NOT NULL DEFAULT 1,
                join_protection_behavior TEXT NOT NULL DEFAULT 'STRICT',
                join_protection_notifications_enabled INTEGER NOT NULL DEFAULT 1,
                privacy_mode INTEGER NOT NULL DEFAULT 0,
                reporting_enabled INTEGER NOT NULL DEFAULT 1,
                moderator_notifications_enabled INTEGER NOT NULL DEFAULT 1,
                scanning_notifications_enabled INTEGER NOT NULL DEFAULT 1,
                reporting_notifications_enabled INTEGER NOT NULL DEFAULT 1,
                channel_link_id INTEGER DEFAULT NULL,
                channel_link_verification_code INTEGER DEFAULT NULL,
                channel_link_thread_id INTEGER DEFAULT NULL
            )
            """
        )
        connection.execute("INSERT INTO chat_configuration (chat_id, enabled) VALUES (-100, 1)")

    database = Database(path)
    try:
        chats = ChatConfigurationManager(database, False)
        ChatConfigurationManager(database, False)  # migrating twice is harmless

        assert chats.resolve(-100).enabled
        assert chats.resolve(-100).block_ads_trackers
    finally:
        database.close()
