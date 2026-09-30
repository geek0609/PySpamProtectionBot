"""Selects the textual content carried by a Telegram message or media caption."""

from collections.abc import Sequence

from pyfederation.records import ContentInput
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message, MessageEntity

from spb.utilities import flat_metadata


def text_or_caption(message: Message) -> str | None:
    """Returns the message text when present, otherwise the media caption."""
    return message.text if message.text is not None else message.caption


def build_scan_input(message: Message, privacy_mode: bool) -> ContentInput:
    """Builds the content submitted for scanning from a message.

    With privacy mode on, only the text is included; otherwise a summary of the message and the
    message itself, with every property it carries, go along as metadata.
    """
    if privacy_mode:
        return ContentInput(text_content=content_to_scan(message))

    author = message.from_user
    fields = {
        "chat_id": message.chat.id,
        "chat_type": message.chat.type.name.lower(),
        "message_id": message.id,
        "author_id": author.id,
        "content_type": "text" if message.text is not None else "caption",
        "has_media": has_media(message),
        "has_link": has_link(message),
        "message_thread_id": message.message_thread_id,
        "sent_at": int(message.date.timestamp()),
        "author_username": author.username,
    }

    return ContentInput(
        text_content=content_to_scan(message),
        note=f"Telegram message {message.id} in chat {message.chat.id}",
        tag="telegram_message",
        confidential=False,
        metadata=flat_metadata.of(message, fields),
    )


def content_to_scan(message: Message) -> str | None:
    """Returns the text of the message if present, otherwise the caption."""
    return text_or_caption(message)


def has_media(message: Message) -> bool:
    """Returns whether the message carries any kind of media."""
    return any(
        media is not None
        for media in (
            message.audio,
            message.document,
            message.photo,
            message.sticker,
            message.video,
            message.contact,
            message.location,
            message.venue,
            message.animation,
            message.voice,
            message.game,
            message.invoice,
            message.video_note,
            message.poll,
            message.dice,
        )
    )


def has_link(message: Message) -> bool:
    """Returns whether the message text or caption carries a link."""
    return _has_link(message.entities) or _has_link(message.caption_entities)


def _has_link(entities: Sequence[MessageEntity] | None) -> bool:
    return any(
        entity.type in (MessageEntityType.URL, MessageEntityType.TEXT_LINK)
        for entity in entities or ()
    )
