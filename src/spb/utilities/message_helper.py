"""Shared helpers for parsing message text, validating identifiers, formatting timestamps, building
keyboards and producing human-readable labels from enum values."""

import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum

from pyfederation.records import ServerInformation
from pyrogram.enums import ChatType
from pyrogram.errors import RPCError
from pyrogram.types import (
    Chat,
    EphemeralMessageParameters,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from spb.classes.language_manager import LanguageManager
from spb.exceptions import FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.language import Language

log = logging.getLogger(__name__)

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def is_private(chat: Chat) -> bool:
    """Returns whether a chat is a one-on-one chat with a user, the Bot API's "private" type,
    which kurigram splits by whether the user is a bot."""
    return chat.type in (ChatType.PRIVATE, ChatType.BOT)


def is_group(chat: Chat) -> bool:
    """Returns whether a chat is a group or supergroup, the Bot API's "group" and "supergroup"
    types; kurigram reports a supergroup with topics as a forum."""
    return chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.FORUM)


def topic_id(message: Message | None) -> int | None:
    """Returns the forum topic a message was posted in, for sending a response to the same topic.

    Only a message flagged as a topic message carries a real topic id; everything else, a forum's
    General topic included, is answered without one.

    Returns:
        The topic id, or None when the message is not in a forum topic.
    """
    if message is None or not message.topic_message:
        return None
    return message.message_thread_id


def ephemeral_to(receiver_user_id: int | None) -> EphemeralMessageParameters | None:
    """Builds the parameters that make a sent message ephemeral to one user.

    A None receiver means the message is not ephemeral and yields None: an ephemeral parameters
    object without a receiver fails the Bot API's validation.
    """
    if receiver_user_id is None:
        return None
    return EphemeralMessageParameters(receiver_user_id=receiver_user_id)


def is_reply_to_topic_header(message: Message) -> bool:
    """Returns whether a message is merely attached to its own forum-topic header rather than
    replying to a specific message.

    Within a forum topic every message is technically a reply to the message that opened the
    thread, and Telegram reports that topic header as the replied message even when the user did not
    explicitly reply to anything. Such an implicit attachment must not be treated as a real reply
    target. A genuine reply to another user's message inside the topic reports that user's message
    instead, whose id never matches the thread header id.
    """
    # Only a message inside a forum topic can be attached to a topic header; the id of the replied
    # message is known even when the message itself could not be fetched.
    if not message.topic_message or message.reply_to_message_id is None:
        return False
    return message.reply_to_message_id == message.message_thread_id


def is_uuid(value: str | None) -> bool:
    """Returns whether the given string is a valid UUID."""
    return value is not None and _UUID.fullmatch(value) is not None


def format_timestamp(epoch_seconds: int) -> str:
    """Formats a Unix epoch second timestamp as a human-readable UTC date-time string."""
    return datetime.fromtimestamp(epoch_seconds, UTC).strftime(_TIMESTAMP_FORMAT)


def format_updated(lm: LanguageManager, lang: Language, epoch_seconds: int) -> str:
    """Formats the last-updated timestamp of a Federation record, which is absent until the record
    is first changed.

    The server leaves the timestamp empty on a record that was never changed and the client reads
    that as 0, which would otherwise be shown as the Unix epoch in 1970.

    Returns:
        The formatted timestamp, or the localized word for "never".
    """
    return (
        format_timestamp(epoch_seconds) if epoch_seconds > 0 else lm.get(lang, "general", "never")
    )


def parse_arguments(text: str | None) -> list[str]:
    """Returns the whitespace-split arguments following the command in the given text, or an empty
    list when there are none."""
    if text is None:
        return []
    return text.strip().partition(" ")[2].split()


def get_message_payload(message: Message) -> str | None:
    """Returns the payload that follows the leading command token, or None when the message carries
    no payload.

    The leading command token is stripped regardless of whether it was invoked as /command payload
    or /command@bot payload: both forms place the payload after the first whitespace, so the
    optional @bot suffix is handled implicitly. The payload is returned trimmed and may itself
    contain whitespace.
    """
    text = message.text
    if text is None:
        return None
    trimmed = text.strip()
    separator = first_whitespace(trimmed)
    if separator == -1:
        return None
    return trimmed[separator + 1 :].strip() or None


def single_argument(message: Message) -> str | None:
    """Returns the payload following the command only when it is exactly one whitespace-separated
    token, or None when there is no payload or the payload contains whitespace."""
    payload = get_message_payload(message)
    if payload is None or first_whitespace(payload) != -1:
        return None
    return payload


def first_whitespace(text: str) -> int:
    """Returns the index of the first whitespace character in the given text, or -1 when there is
    none."""
    return next((index for index, char in enumerate(text) if char.isspace()), -1)


def display_name(value: Enum | None) -> str:
    """Converts an upper-snake-case enum name into human-readable title case, or "Unknown" when
    there is no value."""
    if value is None:
        return "Unknown"
    return humanize(value.name)


def humanize(value: str) -> str:
    """Converts an upper-snake-case string into human-readable title case."""
    return " ".join(part[:1].upper() + part[1:] for part in value.lower().split("_"))


def button(text: str, callback_data: str) -> InlineKeyboardButton:
    """Creates an inline button that carries callback data."""
    return InlineKeyboardButton(text, callback_data=callback_data)


def url_button(text: str, url: str) -> InlineKeyboardButton:
    """Creates an inline button that opens an external URL."""
    return InlineKeyboardButton(text, url=url)


def markup(*rows: Sequence[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    """Builds an inline keyboard from the given rows."""
    return InlineKeyboardMarkup([list(row) for row in rows])


def single_row_markup(*buttons: InlineKeyboardButton) -> InlineKeyboardMarkup:
    """Builds an inline keyboard whose buttons all sit on a single row."""
    return InlineKeyboardMarkup([list(buttons)])


def rows_of_two(buttons: Sequence[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    """Arranges the given buttons into rows of at most two buttons each."""
    return [list(buttons[index : index + 2]) for index in range(0, len(buttons), 2)]


async def server_information(context: HandlerContext) -> ServerInformation | None:
    """Resolves the Federation server information, or None when Federation is not configured or
    the server is unreachable."""
    if not context.federation.is_available:
        return None

    try:
        return await context.federation.server_information()
    except FederationException as e:
        log.warning("Failed to resolve Federation server information: %s", e)
        return None


def server_host(context: HandlerContext) -> str:
    """Returns the Federation server endpoint as it is known to the client, guaranteed to end with
    a trailing slash, or an empty string when Federation is not configured."""
    endpoint = context.federation.endpoint
    if endpoint is None:
        return ""
    return endpoint if endpoint.endswith("/") else endpoint + "/"


def log_outgoing(label: str, payload: object) -> None:
    """Logs the payload of an outgoing API call at DEBUG level.

    Args:
        label: A short label identifying the call (e.g. "open-main-menu").
        payload: The call's arguments, or whatever identifies what is being sent.
    """
    log.debug("Outgoing %s payload: %r", label, payload)


def log_failed(label: str, payload: object, cause: RPCError) -> None:
    """Logs the payload of a failed API call at ERROR level so the exact request that Telegram
    rejected is captured in the logs."""
    log.error("%s failed. Payload: %r", label, payload, exc_info=cause)
