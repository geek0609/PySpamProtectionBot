from spb.classes.sessions.configuration_session_manager import ConfigurationSessionManager
from spb.enums import ConfigurationPage


def test_finds_the_session_showing_a_verification_code() -> None:
    sessions = ConfigurationSessionManager()
    session = sessions.create(42, -100)
    sessions.update_page(session.hash, ConfigurationPage.CHANNEL)
    sessions.update_channel_link_verification_code(session.hash, 123456)

    found = sessions.find_by_channel_link_verification_code(123456)

    assert found is not None
    assert found.hash == session.hash
    assert sessions.find_by_channel_link_verification_code(654321) is None


def test_a_replaced_or_cleared_code_no_longer_finds_the_session() -> None:
    sessions = ConfigurationSessionManager()
    session = sessions.create(42, -100)
    sessions.update_page(session.hash, ConfigurationPage.CHANNEL)

    sessions.update_channel_link_verification_code(session.hash, 111)
    sessions.update_channel_link_verification_code(session.hash, 222)
    assert sessions.find_by_channel_link_verification_code(111) is None
    assert sessions.find_by_channel_link_verification_code(222) is not None

    # Leaving the Chat Linking page discards the code.
    sessions.update_page(session.hash, ConfigurationPage.MAIN)
    assert sessions.find_by_channel_link_verification_code(222) is None


def test_an_invalidated_session_is_not_found() -> None:
    sessions = ConfigurationSessionManager()
    session = sessions.create(42, -100)
    sessions.update_page(session.hash, ConfigurationPage.CHANNEL)
    sessions.update_channel_link_verification_code(session.hash, 333)

    sessions.invalidate(session.hash)

    assert sessions.find_by_channel_link_verification_code(333) is None


def test_remembers_the_ephemeral_message() -> None:
    sessions = ConfigurationSessionManager()
    session = sessions.create_ephemeral(42, -100)

    sessions.update_ephemeral_message_id(session.hash, 7)

    stored = sessions.find(session.hash)
    assert stored is not None
    assert stored.ephemeral
    assert stored.ephemeral_message_id == 7
