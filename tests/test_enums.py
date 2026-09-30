from spb.enums import ConfigurationPage, ScanningBehavior, UpdateType
from tests.support import updates


def test_any_matches_everything() -> None:
    assert UpdateType.ANY.matches(updates.private_message(1, "hello"))
    assert UpdateType.ANY.matches(updates.callback_query(2, "data"))
    assert UpdateType.ANY.matches(updates.business_connection(3, True))


def test_message_matches_messages() -> None:
    assert UpdateType.MESSAGE.matches(updates.private_message(1, "hello"))
    assert not UpdateType.MESSAGE.matches(updates.callback_query(2, "data"))
    assert not UpdateType.MESSAGE.matches(updates.channel_post(3, "post"))


def test_command_matches_commands() -> None:
    assert UpdateType.COMMAND.matches(updates.private_message(1, "/start"))
    assert UpdateType.COMMAND.matches(updates.private_message(2, "/start@bot with payload"))
    assert not UpdateType.COMMAND.matches(updates.private_message(3, "not a command"))
    assert not UpdateType.COMMAND.matches(updates.private_message(4, "text /start in the middle"))
    assert not UpdateType.COMMAND.matches(updates.private_message(5, "/ "))
    assert not UpdateType.COMMAND.matches(updates.private_message(6, None))


def test_command_is_also_a_message() -> None:
    update = updates.private_message(1, "/start")

    assert UpdateType.MESSAGE.matches(update)
    assert UpdateType.COMMAND.matches(update)


def test_other_types_match_their_own() -> None:
    assert UpdateType.CALLBACK_QUERY.matches(updates.callback_query(1, "data"))
    assert UpdateType.CHANNEL_POST.matches(updates.channel_post(2, "post"))
    assert UpdateType.CHAT_MEMBER.matches(updates.chat_member_joined(3))
    assert UpdateType.BUSINESS_MESSAGE.matches(updates.business_message(4, "hello"))
    assert UpdateType.DELETED_BUSINESS_MESSAGES.matches(updates.deleted_business_messages(5))

    assert not UpdateType.CALLBACK_QUERY.matches(updates.private_message(6, "hello"))
    assert not UpdateType.BUSINESS_MESSAGE.matches(updates.private_message(7, "hello"))


def test_resolves_the_most_specific_type() -> None:
    assert UpdateType.of(updates.private_message(1, "/start")) is UpdateType.COMMAND
    assert UpdateType.of(updates.private_message(2, "hello")) is UpdateType.MESSAGE
    assert UpdateType.of(updates.callback_query(3, "data")) is UpdateType.CALLBACK_QUERY
    assert UpdateType.of(updates.channel_post(4, "post")) is UpdateType.CHANNEL_POST


def test_unknown_stored_behavior_is_passive() -> None:
    assert ScanningBehavior.from_string("STRICT") is ScanningBehavior.STRICT
    assert ScanningBehavior.from_string("NO_SUCH_LEVEL") is ScanningBehavior.PASSIVE
    assert ScanningBehavior.from_string(None) is ScanningBehavior.PASSIVE


def test_configuration_pages_cycle() -> None:
    assert ConfigurationPage.SCANNING.previous() is ConfigurationPage.CHANNEL
    assert ConfigurationPage.CHANNEL.next() is ConfigurationPage.SCANNING
    assert ConfigurationPage.MAIN.next() is ConfigurationPage.SCANNING
    assert ConfigurationPage.PRIVACY.next() is ConfigurationPage.MODERATOR_NOTIFICATIONS
