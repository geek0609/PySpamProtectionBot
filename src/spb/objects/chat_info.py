"""Cached snapshot of a Telegram chat."""

from dataclasses import dataclass

from pyrogram.enums import ChatType


@dataclass(frozen=True, slots=True)
class ChatInfo:
    """Cached snapshot of a Telegram chat.

    Attributes:
        chat_id: The Telegram chat id.
        name: The chat's display name (the title of a group or channel, the first name of a user).
        type: The Telegram chat type.
    """

    chat_id: int
    name: str
    type: ChatType
