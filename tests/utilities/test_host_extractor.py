import random
import time

from pyrogram.enums import ChatType, MessageEntityType
from pyrogram.types import Chat, Message, MessageEntity, Photo, User
from pyrogram.types.messages_and_media.message import Str

from spb.utilities import host_canonicalizer, host_extractor
from spb.utilities.host_extractor import MAX_HOSTS_PER_MESSAGE
from tests.support.updates import DATE


def extract(text: str) -> list[str]:
    return host_extractor.extract_from_text(text, None, MAX_HOSTS_PER_MESSAGE)


def entity(
    type: MessageEntityType, offset: int, length: int, url: str | None = None
) -> MessageEntity:
    return MessageEntity(type=type, offset=offset, length=length, url=url)


def test_finds_bare_domains_and_addresses() -> None:
    assert extract("Visit evil.com or 8.8.8.8, also 2001:4860:4860::8888.") == [
        "evil.com",
        "8.8.8.8",
        "2001:4860:4860::8888",
    ]


def test_finds_urls_written_in_prose() -> None:
    text = (
        'See (https://evil.com/login). Or "http://phish.net"! '
        "https://en.wikipedia.org/wiki/Foo_(bar)"
    )

    assert extract(text) == ["evil.com", "phish.net", "en.wikipedia.org"]


def test_does_not_pick_apart_url_paths() -> None:
    assert extract("https://evil.com/files/readme.md/other.org?x=1") == ["evil.com"]
    assert extract("evil.com/downloads/other.org") == ["evil.com"]


def test_skips_emails_and_mentions() -> None:
    assert extract("mail john.doe@evil.com or ping @evil_bot") == []


def test_skips_words_that_only_look_like_hosts() -> None:
    text = (
        "e.g. version 1.2.3.4.5, pi is 3.14, see setup.py and Main.java at 12:30:45, "
        "mac aa:bb:cc:dd:ee:ff, std::vector, example.com, 192.168.1.1, 127.0.0.1"
    )

    assert extract(text) == []


def test_skips_hosts_glued_to_other_words() -> None:
    assert extract("пример.com x_evil.com evil.com_x /path/evil.com") == []


def test_deduplicates_canonical_forms() -> None:
    assert extract("evil.com EVIL.COM https://Evil.com/ evil.com.") == ["evil.com"]
    assert extract("8.8.8.8 ::ffff:8.8.8.8 http://0x08080808/") == ["8.8.8.8"]


def test_uses_telegram_link_entities() -> None:
    entities = [
        entity(MessageEntityType.TEXT_LINK, 0, 10, "https://hidden.evil.com/x"),
        entity(MessageEntityType.URL, 15, 9),
    ]

    hosts = host_extractor.extract_from_text(
        "Click here, or пример.рф", entities, MAX_HOSTS_PER_MESSAGE
    )

    assert hosts == ["hidden.evil.com", "xn--e1afmkfd.xn--p1ai"]


def test_measures_entity_offsets_in_utf16() -> None:
    # The emoji takes two UTF-16 code units, as Telegram counts them.
    entities = [entity(MessageEntityType.URL, 3, 8)]

    hosts = host_extractor.extract_from_text("😀 evil.com", entities, MAX_HOSTS_PER_MESSAGE)

    assert hosts == ["evil.com"]


def test_accepts_file_like_domains_only_when_telegram_linked_them() -> None:
    text = "get setup.zip"

    assert extract(text) == []
    assert host_extractor.extract_from_text(
        text, [entity(MessageEntityType.URL, 4, 9)], MAX_HOSTS_PER_MESSAGE
    ) == ["setup.zip"]


def test_email_entities_are_not_mined_for_domains() -> None:
    entities = [entity(MessageEntityType.EMAIL, 9, 10)]

    assert host_extractor.extract_from_text("write to a@evil.com", entities, 20) == []


def test_tolerates_malformed_entities() -> None:
    entities = [
        entity(MessageEntityType.URL, -1, 3),
        entity(MessageEntityType.URL, 5, 100),
        entity(MessageEntityType.URL, 2**31 - 1, 2**31 - 1),
        entity(MessageEntityType.URL, 0, 0),
        entity(MessageEntityType.TEXT_LINK, 0, 4),
        entity(MessageEntityType.TEXT_LINK, 0, 4, "not a url"),
    ]

    assert host_extractor.extract_from_text("evil.com", entities, MAX_HOSTS_PER_MESSAGE) == [
        "evil.com"
    ]


def test_tolerates_empty_input() -> None:
    assert host_extractor.extract_from_text(None, None, 10) == []
    assert host_extractor.extract_from_text("", [], 10) == []
    assert host_extractor.extract_from_text("evil.com", None, 0) == []


def test_caps_the_number_of_hosts() -> None:
    hosts = extract("".join(f"host{index}.com " for index in range(100)))

    assert len(hosts) == MAX_HOSTS_PER_MESSAGE
    assert hosts[0] == "host0.com"


def test_pathological_input_finishes_quickly() -> None:
    inputs = [
        "a." * 50_000,
        "http://" + "a" * 100_000,
        ":" * 100_000,
        "1." * 50_000,
        "a:" * 50_000,
        "http://" * 20_000,
        "(" * 50_000 + "http://evil.com" + ")" * 50_000,
    ]

    started = time.monotonic()
    for text in inputs:
        assert len(extract(text)) <= MAX_HOSTS_PER_MESSAGE
    assert time.monotonic() - started < 5


def test_random_input_never_raises_and_only_yields_canonical_hosts() -> None:
    unicode = "".join(map(chr, (0x43F, 0x440, 0x3002, 0xFF0C, 0x1F600)))
    alphabet = "aeXz09.-:/\\@[]%_ ()\"'.:0x7f8\t\n#?=+" + unicode
    seeds = ["http://", "https://", "evil.com", "8.8.8.8", "2001:4860::8888", "xn--", "::ffff:"]
    generator = random.Random(20260924)
    for _ in range(5_000):
        pieces = [
            generator.choice(seeds) if generator.randrange(3) == 0 else generator.choice(alphabet)
            for _ in range(generator.randrange(12))
        ]
        text = "".join(pieces)
        entities = [
            entity(
                MessageEntityType.URL,
                generator.randrange(len(text) + 2),
                generator.randrange(len(text) + 2),
            ),
            entity(MessageEntityType.TEXT_LINK, 0, 1, text),
        ]
        for host in host_extractor.extract_from_text(text, entities, MAX_HOSTS_PER_MESSAGE):
            as_url = f"http://[{host}]/" if ":" in host else f"http://{host}/"
            assert host_canonicalizer.from_url(as_url) == host, f"{host} from {text!r}"
            assert host == host.lower()
            assert all(0x20 < ord(character) < 0x7F for character in host)


def test_reads_caption_when_there_is_no_text() -> None:
    message = Message(
        id=7,
        date=DATE,
        chat=Chat(id=-100, type=ChatType.SUPERGROUP),
        from_user=User(id=42, is_bot=False, first_name="A"),
        photo=Photo(file_id="f", file_unique_id="u", width=1, height=1, file_size=1, date=DATE),
        caption=Str("free prizes at evil.com"),
        caption_entities=[entity(MessageEntityType.URL, 15, 8)],
    )

    assert host_extractor.extract(message) == ["evil.com"]
