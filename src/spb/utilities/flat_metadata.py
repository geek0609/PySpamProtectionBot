"""Flattens Telegram objects into metadata a Federation server accepts.

Federation metadata is a flat JSON object of scalar values, bounded in size. A Telegram object is
flattened into dotted keys (from_user.id, entities.0.type), dropping empty and unsupported values,
cutting long strings at a character boundary, and dropping the deepest properties first when the
whole does not fit. Whenever anything had to give way, the result carries _truncated: true.
"""

import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from pyrogram.types import Object, User

log = logging.getLogger(__name__)

TRUNCATED_KEY = "_truncated"
MAX_ENCODED_BYTES = 8_000
MAX_KEY_LENGTH = 64
MAX_STRING_BYTES = 1_000

_PRIVATE_USER_FIELDS = {"dc_id", "status", "last_online_date", "next_offline_date", "restrictions"}
"""User fields only an MTProto client sees. The Bot API never exposes them, so leaving them out
keeps what the bot publishes about a user to what a Bot API bot could publish."""

_JSON_ESCAPES = {'"', "\\", "\b", "\f", "\n", "\r", "\t"}

type Scalar = bool | int | float | str


@dataclass(frozen=True, slots=True)
class _Entry:
    key: str
    value: Scalar
    depth: int
    order: int


def of(source: object, fields: Mapping[str, object] | None = None) -> dict[str, Scalar]:
    """Flattens an object into metadata.

    Args:
        source: The Telegram object to flatten, or None.
        fields: Explicit fields placed first; they win over properties of the same name.

    Returns:
        The metadata.
    """
    truncated = [False]
    properties: dict[str, Scalar] = {}
    if source is not None:
        _flatten(None, _tree(source), properties, truncated)
    return _combine(fields or {}, properties, truncated)


def with_fields(
    fields: Mapping[str, object], metadata: Mapping[str, object] | None
) -> dict[str, Scalar]:
    """Places explicit fields ahead of an existing metadata snapshot and fits the whole again."""
    truncated = [False]
    properties: dict[str, Scalar] = {}
    for key, value in (metadata or {}).items():
        if key == TRUNCATED_KEY:
            truncated[0] |= value is True
            continue
        scalar = _scalar(value, truncated)
        if scalar is not None:
            properties[key] = scalar
    return _combine(fields, properties, truncated)


def _tree(value: object) -> object:
    """Converts a Telegram object into plain JSON-like values, keeping only public attributes."""
    if isinstance(value, Object):
        attributes = {
            name: attribute
            for name, attribute in vars(value).items()
            if not name.startswith("_")
            and not (isinstance(value, User) and name in _PRIVATE_USER_FIELDS)
        }
        return {name: _tree(attribute) for name, attribute in attributes.items()}
    if isinstance(value, Mapping):
        return {str(key): _tree(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_tree(item) for item in value]
    if isinstance(value, Enum):
        return value.name.lower()
    if isinstance(value, datetime):
        return int(value.timestamp())
    return value


def _combine(
    fields: Mapping[str, object], properties: dict[str, Scalar], truncated: list[bool]
) -> dict[str, Scalar]:
    entries = []
    order = 0
    for key, value in fields.items():
        scalar = _scalar(value, truncated)
        if scalar is not None:
            entries.append(_Entry(key, scalar, -1, order))
            order += 1
    for key, scalar in properties.items():
        if key not in fields:
            entries.append(_Entry(key, scalar, key.count("."), order))
            order += 1
    return _fit(entries, truncated[0])


def _flatten(path: str | None, node: object, out: dict[str, Scalar], truncated: list[bool]) -> None:
    if node is None:
        return

    if isinstance(node, dict):
        for key, value in node.items():
            _flatten(key if path is None else f"{path}.{key}", value, out, truncated)
        return

    if isinstance(node, list):
        for index, value in enumerate(node):
            _flatten(str(index) if path is None else f"{path}.{index}", value, out, truncated)
        return

    if path is None:
        # A bare scalar has no property name to file it under.
        return

    scalar = _scalar(node, truncated)
    if scalar is not None:
        out.setdefault(path, scalar)


def _scalar(value: object, truncated: list[bool]) -> Scalar | None:
    if value is None:
        return None
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str | Enum):
        text = replace_lone_surrogates(value.name if isinstance(value, Enum) else str(value))
        cut = truncate_utf8(text, MAX_STRING_BYTES)
        if len(cut) != len(text):
            truncated[0] = True
        return cut or None
    log.debug("Dropping metadata value of unsupported type %s", type(value).__name__)
    return None


def _fit(entries: list[_Entry], already_truncated: bool) -> dict[str, Scalar]:
    """Keeps the shallowest entries that fit in the encoded size budget, in their original order."""
    truncated = already_truncated
    candidates = []
    for entry in entries:
        if not entry.key or len(entry.key.encode("utf-8", "surrogatepass")) > MAX_KEY_LENGTH:
            truncated = True
            continue
        candidates.append(entry)

    # Room for the braces, and for the truncation marker should it be needed.
    budget = MAX_ENCODED_BYTES - 2 - (_encoded_size(TRUNCATED_KEY, True) + 1)
    used = 0
    kept: list[_Entry] = []
    for entry in sorted(candidates, key=lambda entry: (entry.depth, entry.order)):
        size = _encoded_size(entry.key, entry.value) + (1 if kept else 0)
        if used + size > budget:
            truncated = True
            continue
        used += size
        kept.append(entry)

    metadata: dict[str, Scalar] = {}
    for entry in sorted(kept, key=lambda entry: entry.order):
        metadata.setdefault(entry.key, entry.value)
    if truncated:
        metadata[TRUNCATED_KEY] = True
    return metadata


def _encoded_size(key: str, value: Scalar) -> int:
    return encoded_length(key) + 1 + encoded_length(value)


def encoded_length(value: Any) -> int:
    """Returns the length of a value encoded as JSON with every non-ASCII character escaped, the
    largest encoding any encoder produces."""
    if isinstance(value, str):
        length = 2
        for character in value:
            code = ord(character)
            if character in _JSON_ESCAPES:
                length += 2
            elif code < 0x20 or code >= 0x80:
                # A character outside the Basic Multilingual Plane is two escaped surrogates.
                length += 12 if code > 0xFFFF else 6
            else:
                length += 1
        return length
    return len(json.dumps(value))


def replace_lone_surrogates(text: str) -> str:
    """Replaces every surrogate that is not half of a pair with U+FFFD; UTF-8 cannot encode one."""
    result = []
    index = 0
    while index < len(text):
        character = text[index]
        paired = (
            "\ud800" <= character <= "\udbff"
            and index + 1 < len(text)
            and "\udc00" <= text[index + 1] <= "\udfff"
        )
        if paired:
            result.append(text[index : index + 2])
            index += 2
            continue
        result.append("\ufffd" if "\ud800" <= character <= "\udfff" else character)
        index += 1
    return "".join(result)


def truncate_utf8(text: str, max_bytes: int) -> str:
    """Cuts a string to at most the given number of UTF-8 bytes, at a character boundary."""
    size = 0
    for index, character in enumerate(text):
        code = ord(character)
        width = 1 if code < 0x80 else 2 if code < 0x800 else 3 if code < 0x10000 else 4
        if size + width > max_bytes:
            return text[:index]
        size += width
    return text
