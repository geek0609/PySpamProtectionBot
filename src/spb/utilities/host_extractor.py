"""Finds the domain names and IP addresses mentioned in a message's text or caption.

Hosts are gathered from three places, in this order:

1. the links Telegram itself marked up: url entities, and the targets of text_link entities, which
   are not visible in the text at all;
2. scheme:// URLs written in the text;
3. bare domain names and IP addresses in the rest of the text.

Every candidate is canonicalized by host_canonicalizer, which rejects anything malformed or
non-public, and the result is de-duplicated and capped. Email addresses and @mentions are
deliberately skipped: they name an account, not a host. The text is untrusted and may be
arbitrarily malformed; nothing here raises on bad input, and the regular expressions used run in
linear time.

Telegram measures entity offsets in UTF-16 code units, so the text is worked on with every
character outside the Basic Multilingual Plane split into its two surrogates.
"""

import re
from collections.abc import Sequence

from pyrogram.enums import MessageEntityType
from pyrogram.parser.utils import add_surrogates
from pyrogram.types import Message, MessageEntity

from spb.utilities import host_canonicalizer

MAX_HOSTS_PER_MESSAGE = 20
"""The most hosts taken from a single message, bounding the Federation calls it can cause."""

_SCHEME_URL = re.compile(
    r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]{0,31}://[^\s<>\"'`，、；：！？「」『』（）【】《》]+"  # noqa: RUF001
)
"""A scheme:// URL. It runs until whitespace or a character that cannot appear unescaped in a link
as written in prose (quotes, angle brackets, CJK punctuation)."""

_DOMAIN_OR_IPV4_RUN = re.compile(r"[A-Za-z0-9.\-]+")
"""A run that may hold a domain name or IPv4 address; boundaries are checked in code."""

_IPV6_RUN = re.compile(r"[0-9A-Fa-f:.]+")
"""A run that may hold an IPv6 address; boundaries are checked in code."""

_TRAILING_PUNCTUATION = ".,;:!?'\")]}>»”’…"  # noqa: RUF001
"""Characters a sentence may put right after a link without them being part of it."""

_BRACKETS = "()[]{}"


def extract(message: Message) -> list[str]:
    """Returns the hosts mentioned in a message's text, or in its caption when it has no text.

    Returns:
        The canonical hosts in order of appearance, at most MAX_HOSTS_PER_MESSAGE.
    """
    if message.text is not None:
        return extract_from_text(message.text, message.entities, MAX_HOSTS_PER_MESSAGE)
    return extract_from_text(message.caption, message.caption_entities, MAX_HOSTS_PER_MESSAGE)


def extract_from_text(
    text: str | None, entities: Sequence[MessageEntity] | None, limit: int
) -> list[str]:
    """Returns the hosts mentioned in a text.

    Args:
        text: The text, or None.
        entities: The Telegram entities of that text, or None.
        limit: The most hosts to return.

    Returns:
        The canonical hosts in order of appearance.
    """
    if limit <= 0 or (not text and not entities):
        return []

    hosts: dict[str, None] = {}
    source = add_surrogates(text or "")
    # Regions already understood (links, emails) are blanked so the bare-word pass does not pick
    # apart their paths and local parts: "https://a.com/notes.md" must not yield notes.md.
    remaining = list(source)

    for entity in entities or ():
        if entity.type is MessageEntityType.TEXT_LINK:
            _add(hosts, host_canonicalizer.from_url(entity.url), limit)
        elif entity.type in (MessageEntityType.URL, MessageEntityType.EMAIL):
            link = _slice(source, entity)
            if link is None:
                continue
            if entity.type is MessageEntityType.URL:
                _add(hosts, host_canonicalizer.from_url(_restore(link)), limit)
            _blank(remaining, entity.offset, entity.offset + entity.length)

    for url in _SCHEME_URL.finditer("".join(remaining)):
        end = _trim_trailing_punctuation(remaining, url.start(), url.end())
        _add(
            hosts,
            host_canonicalizer.from_url(_restore("".join(remaining[url.start() : end]))),
            limit,
        )
        _blank(remaining, url.start(), url.end())

    rest = "".join(remaining)
    for run in _DOMAIN_OR_IPV4_RUN.finditer(rest):
        candidate = run.group().strip(".-")
        if "." not in candidate or not _stands_alone(rest, run.start(), run.end(), "@/\\:_", "@_"):
            continue
        _add(hosts, host_canonicalizer.from_text(candidate), limit)

    for run in _IPV6_RUN.finditer(rest):
        candidate = run.group().rstrip(".")
        if candidate.count(":") < 2 or not _stands_alone(rest, run.start(), run.end(), "/\\_", "_"):
            continue
        _add(hosts, host_canonicalizer.from_text(candidate), limit)

    return list(hosts)


def _slice(text: str, entity: MessageEntity) -> str | None:
    """Returns the part of the text an entity covers, None when the entity lies outside it."""
    if entity.offset < 0 or entity.length <= 0 or entity.offset + entity.length > len(text):
        return None
    return text[entity.offset : entity.offset + entity.length]


def _restore(units: str) -> str:
    """Joins the surrogate pairs of a UTF-16 slice back into characters."""
    return units.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def _stands_alone(
    text: str, start: int, end: int, forbidden_before: str, forbidden_after: str
) -> bool:
    """Returns whether a run is not glued to a letter, a digit or a forbidden character."""
    if start > 0:
        before = text[start - 1]
        if _is_letter_or_digit(before) or before in forbidden_before:
            return False

    if end < len(text):
        after = text[end]
        return not _is_letter_or_digit(after) and after not in forbidden_after

    return True


def _is_letter_or_digit(character: str) -> bool:
    return character.isalpha() or character.isdecimal()


def _trim_trailing_punctuation(text: list[str], start: int, end: int) -> int:
    """Trims sentence punctuation from the end of a URL, keeping closing brackets it opened.

    Bracket balance is counted once and kept current while trimming, so a URL followed by thousands
    of closing brackets costs linear time, not quadratic.
    """
    counts = [0] * len(_BRACKETS)
    for character in text[start:end]:
        index = _BRACKETS.find(character)
        if index >= 0:
            counts[index] += 1

    while end > start and text[end - 1] in _TRAILING_PUNCTUATION:
        closing = _BRACKETS.find(text[end - 1])
        if closing >= 0 and closing % 2 == 1 and counts[closing - 1] >= counts[closing]:
            break
        if closing >= 0:
            counts[closing] -= 1
        end -= 1
    return end


def _blank(text: list[str], start: int, end: int) -> None:
    for index in range(max(0, start), min(len(text), end)):
        text[index] = " "


def _add(hosts: dict[str, None], host: str | None, limit: int) -> None:
    if host is not None and len(hosts) < limit:
        hosts[host] = None
