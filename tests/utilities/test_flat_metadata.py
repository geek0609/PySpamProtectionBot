import json
from collections.abc import Mapping

from pyrogram.enums import ChatType, MessageEntityType
from pyrogram.types import Chat, Message, MessageEntity, User
from pyrogram.types.messages_and_media.message import Str

from spb.utilities import flat_metadata
from spb.utilities.flat_metadata import TRUNCATED_KEY
from tests.support.updates import DATE

AUTHOR = User(id=42, is_bot=False, first_name="Ann", language_code="en")
GROUP = Chat(id=-100, type=ChatType.SUPERGROUP, title="Group")

EMOJI = chr(0x1F600)


def message(
    text: str,
    entities: list[MessageEntity] | None = None,
    reply_to_message: Message | None = None,
) -> Message:
    return Message(
        id=7,
        date=DATE,
        chat=GROUP,
        from_user=AUTHOR,
        text=Str(text),
        entities=entities,
        reply_to_message=reply_to_message,
    )


def link(offset: int, url: str) -> MessageEntity:
    return MessageEntity(type=MessageEntityType.TEXT_LINK, offset=offset, length=1, url=url)


def assert_valid_metadata(metadata: Mapping[str, object]) -> None:
    for key, value in metadata.items():
        assert key
        assert len(key.encode("utf-8")) <= 64, key
        assert isinstance(value, str | int | float), key
        if isinstance(value, str):
            assert value, key
            assert len(value.encode("utf-8", "surrogatepass")) <= 1000, key
    compact = (",", ":")
    encoded = json.dumps(metadata, ensure_ascii=False, separators=compact)
    assert len(encoded.encode("utf-8", "surrogatepass")) <= 8000
    # Also within budget when every non-ASCII character is escaped, as ASCII-only encoders do.
    assert len(json.dumps(metadata, separators=compact)) <= 8000


def test_flattens_nested_objects_and_arrays_under_dotted_paths() -> None:
    reply = Message(
        id=6,
        date=DATE,
        chat=Chat(id=-100, type=ChatType.SUPERGROUP),
        from_user=User(id=43, is_bot=True, first_name="Bot"),
        text=Str("x"),
    )
    command = MessageEntity(type=MessageEntityType.BOT_COMMAND, offset=0, length=6)

    metadata = flat_metadata.of(message("/start hi", [command], reply))

    assert metadata["id"] == 7
    assert metadata["from_user.id"] == 42
    assert metadata["from_user.is_bot"] is False
    assert metadata["from_user.language_code"] == "en"
    assert metadata["chat.id"] == -100
    assert metadata["chat.title"] == "Group"
    assert metadata["chat.type"] == "supergroup"
    assert metadata["date"] == 1_700_000_000
    assert metadata["entities.0.type"] == "bot_command"
    assert metadata["entities.0.length"] == 6
    assert metadata["reply_to_message.from_user.id"] == 43
    assert metadata["reply_to_message.text"] == "x"
    # Absent properties are left out, not sent as null.
    assert "caption" not in metadata
    assert "from_user.username" not in metadata
    assert TRUNCATED_KEY not in metadata
    assert_valid_metadata(metadata)


def test_keeps_property_order() -> None:
    assert list(flat_metadata.of(message("hi"))) == [
        "id",
        "from_user.id",
        "from_user.is_bot",
        "from_user.first_name",
        "from_user.language_code",
        "date",
        "chat.id",
        "chat.type",
        "chat.title",
        "text",
    ]


def test_explicit_fields_come_first_and_win_over_properties() -> None:
    fields = {"reporter_id": 99, "id": 1234, "absent": None}

    metadata = flat_metadata.of(message("hi"), fields)

    assert list(metadata)[:2] == ["reporter_id", "id"]
    assert metadata["id"] == 1234
    assert "absent" not in metadata


def test_with_fields_refits_a_snapshot() -> None:
    snapshot = flat_metadata.of(message("hi"))

    metadata = flat_metadata.with_fields({"reporter_id": 99}, snapshot)

    assert next(iter(metadata)) == "reporter_id"
    assert metadata["from_user.id"] == 42
    assert len(metadata) == len(snapshot) + 1
    assert flat_metadata.with_fields({"a": 1}, None) == {"a": 1}


def test_long_strings_are_cut_at_a_character_boundary() -> None:
    # 999 ASCII bytes then a 4-byte emoji: the emoji does not fit and must not be split.
    metadata = flat_metadata.of(message("a" * 999 + EMOJI + "tail"))

    assert metadata["text"] == "a" * 999
    assert metadata[TRUNCATED_KEY] is True
    assert_valid_metadata(metadata)

    e_acute = chr(0xE9)
    assert flat_metadata.truncate_utf8(e_acute * 600, 1000) == e_acute * 500
    assert flat_metadata.truncate_utf8("ab" + chr(0x20AC), 4) == "ab"


def test_empty_strings_and_overlong_keys_are_dropped() -> None:
    metadata = flat_metadata.of(None, {"empty": "", "k" * 65: 1, "k" * 64: 2})

    assert "empty" not in metadata
    assert metadata["k" * 64] == 2
    assert metadata[TRUNCATED_KEY] is True
    assert_valid_metadata(metadata)


def test_oversized_objects_keep_the_shallowest_properties_within_budget() -> None:
    long_text = "x" * 4000
    entities = [link(index, f"https://example.com/{'p' * 60}{index}") for index in range(300)]
    reply = Message(
        id=6, date=DATE, chat=Chat(id=-100, type=ChatType.SUPERGROUP), text=Str(long_text)
    )

    metadata = flat_metadata.of(message(long_text, entities, reply))

    assert_valid_metadata(metadata)
    assert metadata[TRUNCATED_KEY] is True
    assert metadata["id"] == 7
    assert metadata["from_user.id"] == 42
    assert metadata["chat.id"] == -100
    assert len(str(metadata["text"])) == 1000
    assert "reply_to_message.id" in metadata
    # Deep entries are what gives way.
    assert "entities.299.url" not in metadata


def test_escape_heavy_text_stays_within_budget_for_any_encoder() -> None:
    line_separator = chr(0x2028)
    heavy = line_separator * 300 + EMOJI * 100 + '"' + "\\" + chr(1)

    metadata = flat_metadata.of(message(heavy, [link(0, heavy) for _ in range(50)]))

    assert_valid_metadata(metadata)
    assert metadata[TRUNCATED_KEY] is True
    assert metadata["from_user.id"] == 42


def test_lone_surrogates_are_replaced() -> None:
    high, low = chr(0xD83D), chr(0xDE00)
    replacement = chr(0xFFFD)

    metadata = flat_metadata.of(message("a" + chr(0xD800) + "b" + chr(0xDC00) + high + low))

    assert metadata["text"] == "a" + replacement + "b" + replacement + high + low


def test_users_keep_their_property_names() -> None:
    assert flat_metadata.of(AUTHOR) == {
        "id": 42,
        "is_bot": False,
        "first_name": "Ann",
        "language_code": "en",
    }


def test_leaves_out_user_fields_only_mtproto_sees() -> None:
    user = User(id=42, is_bot=False, first_name="Ann", dc_id=4, last_online_date=DATE)

    assert flat_metadata.of(user) == {"id": 42, "is_bot": False, "first_name": "Ann"}


def test_nothing_to_flatten() -> None:
    assert flat_metadata.of(None) == {}
    assert flat_metadata.of("a bare string has no property names") == {}
    assert flat_metadata.of([]) == {}
