from dataclasses import replace

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
