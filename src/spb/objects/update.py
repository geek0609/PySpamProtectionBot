"""An incoming update, in the shape of a Telegram Bot API update."""

from collections.abc import Sequence
from dataclasses import dataclass

from pyrogram.types import (
    BusinessConnection,
    CallbackQuery,
    ChatJoinRequest,
    ChatMemberUpdated,
    Message,
)


@dataclass(frozen=True, slots=True)
class Update:
    """One incoming update, carrying exactly one of the fields below.

    Telegram delivers updates over MTProto as separate kinds of event rather than as the single
    update object of the Bot API. The bot's connection wraps each one in this envelope, named after
    the Bot API update fields, so the dispatcher, the update types and every handler see one shape
    whatever arrived.

    Attributes:
        update_id: The sequence number the connection assigned the update, for logging.
        message: A new message in a private chat or a group.
        channel_post: A new post in a channel the bot is a member of.
        callback_query: An inline button press.
        my_chat_member: The bot's own membership in a chat changed.
        chat_member: Another member's status in a chat changed.
        chat_join_request: A request to join a chat the bot administers.
        business_connection: A Telegram Business account connected the bot or changed its
            connection.
        business_message: A message received through a connected business account.
        edited_business_message: A business message that was edited.
        deleted_business_messages: Business messages that were deleted, each carrying its chat, id
            and business connection.
    """

    update_id: int
    message: Message | None = None
    channel_post: Message | None = None
    callback_query: CallbackQuery | None = None
    my_chat_member: ChatMemberUpdated | None = None
    chat_member: ChatMemberUpdated | None = None
    chat_join_request: ChatJoinRequest | None = None
    business_connection: BusinessConnection | None = None
    business_message: Message | None = None
    edited_business_message: Message | None = None
    deleted_business_messages: Sequence[Message] | None = None
