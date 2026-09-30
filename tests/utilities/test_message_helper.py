from pyrogram.enums import ChatType
from pyrogram.types import Chat, Message, User
from pyrogram.types.messages_and_media.message import Str

from spb.utilities import message_helper
from tests.support import updates


def reply_to_50(message_thread_id: int | None = None, topic_message: bool | None = None) -> Message:
    """Builds a /report message replying to message 50."""
    chat = Chat(id=-1001234567890, type=ChatType.SUPERGROUP, title="Group")
    target = Message(
        id=50,
        date=updates.DATE,
        chat=chat,
        from_user=User(id=99, is_bot=False, first_name="Spammer"),
        text=Str("buy now"),
    )
    return Message(
        id=51,
        date=updates.DATE,
        chat=chat,
        from_user=User(id=42, is_bot=False, first_name="Reporter"),
        text=Str("/report"),
        message_thread_id=message_thread_id,
        topic_message=topic_message,
        reply_to_message_id=50,
        reply_to_message=target,
    )


def test_chat_types_are_grouped_as_the_bot_api_groups_them() -> None:
    def chat(chat_type: ChatType) -> Chat:
        return Chat(id=1, type=chat_type)

    assert message_helper.is_private(chat(ChatType.PRIVATE))
    assert message_helper.is_private(chat(ChatType.BOT))
    assert not message_helper.is_private(chat(ChatType.GROUP))
    assert message_helper.is_group(chat(ChatType.GROUP))
    assert message_helper.is_group(chat(ChatType.SUPERGROUP))
    assert message_helper.is_group(chat(ChatType.FORUM))
    assert not message_helper.is_group(chat(ChatType.CHANNEL))


def test_a_message_attached_to_its_forum_topic_header_is_not_a_real_reply() -> None:
    assert message_helper.is_reply_to_topic_header(reply_to_50(50, True))


def test_a_reply_to_another_message_inside_a_forum_topic_is_a_real_reply() -> None:
    assert not message_helper.is_reply_to_topic_header(reply_to_50(12, True))


def test_a_reply_with_no_thread_at_all_is_a_real_reply() -> None:
    assert not message_helper.is_reply_to_topic_header(reply_to_50())


def test_a_message_inside_a_forum_topic_is_answered_in_that_topic() -> None:
    assert message_helper.topic_id(reply_to_50(12, True)) == 12


def test_a_message_with_no_thread_at_all_has_no_topic() -> None:
    assert message_helper.topic_id(reply_to_50()) is None


def test_the_payload_follows_the_command_token() -> None:
    assert message_helper.parse_arguments("/blacklist  a   b ") == ["a", "b"]
    assert message_helper.parse_arguments("/blacklist") == []

    message = reply_to_50()
    message.text = Str("/info@testbot  one two ")
    assert message_helper.get_message_payload(message) == "one two"
    assert message_helper.single_argument(message) is None

    message.text = Str("/info one")
    assert message_helper.single_argument(message) == "one"


def test_enum_names_are_humanized() -> None:
    assert message_helper.display_name(ChatType.SUPERGROUP) == "Supergroup"
    assert message_helper.display_name(None) == "Unknown"
    assert message_helper.humanize("ILLEGAL_CONTENT") == "Illegal Content"


def test_buttons_are_arranged_in_rows_of_two() -> None:
    buttons = [message_helper.button(str(index), f"cb:{index}") for index in range(3)]

    rows = message_helper.rows_of_two(buttons)

    assert [[button.text for button in row] for row in rows] == [["0", "1"], ["2"]]


def test_a_message_with_no_ephemeral_receiver_is_a_regular_message() -> None:
    assert message_helper.ephemeral_to(None) is None


def test_a_message_with_an_ephemeral_receiver_is_addressed_to_that_user() -> None:
    parameters = message_helper.ephemeral_to(42)

    assert parameters is not None
    assert parameters.receiver_user_id == 42
