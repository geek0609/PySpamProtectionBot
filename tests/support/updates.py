"""Builds the updates the tests feed to the bot."""

from datetime import UTC, datetime

from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import (
    BusinessConnection,
    CallbackQuery,
    Chat,
    ChatMember,
    ChatMemberUpdated,
    Message,
    User,
)
from pyrogram.types.messages_and_media.message import Str

from spb.objects.update import Update

DATE = datetime.fromtimestamp(1_700_000_000, UTC)

TESTER = User(id=42, is_bot=False, first_name="Tester", username="tester")


def private_message(update_id: int, text: str | None) -> Update:
    return message(update_id, 42, ChatType.PRIVATE, text)


def group_message(update_id: int, text: str | None) -> Update:
    return message(update_id, -1001234567890, ChatType.SUPERGROUP, text)


def message(update_id: int, chat_id: int, chat_type: ChatType, text: str | None) -> Update:
    chat = Chat(id=chat_id, type=chat_type, title="Test Chat")
    return Update(
        update_id,
        message=Message(
            id=update_id,
            date=DATE,
            chat=chat,
            from_user=TESTER,
            text=Str(text) if text is not None else None,
        ),
    )


def channel_post(update_id: int, text: str) -> Update:
    chat = Chat(id=-1009876543210, type=ChatType.CHANNEL, title="Test Channel")
    return Update(
        update_id, channel_post=Message(id=update_id, date=DATE, chat=chat, text=Str(text))
    )


def callback_query(update_id: int, data: str) -> Update:
    chat = Chat(id=42, type=ChatType.PRIVATE)
    menu = Message(id=update_id, date=DATE, chat=chat, text=Str("menu"))
    query = CallbackQuery(
        id=f"cb-{update_id}",
        from_user=User(id=42, is_bot=False, first_name="Tester"),
        chat_instance="instance",
        message=menu,
        data=data,
    )
    return Update(update_id, callback_query=query)


def business_connection(update_id: int, enabled: bool) -> Update:
    owner = User(id=42, is_bot=False, first_name="Owner", username="owner")
    connection = BusinessConnection(id="conn-1", user=owner, dc_id=2, date=DATE, is_enabled=enabled)
    return Update(update_id, business_connection=connection)


def business_message(update_id: int, text: str) -> Update:
    chat = Chat(id=99, type=ChatType.PRIVATE)
    stranger = User(id=99, is_bot=False, first_name="Stranger")
    received = Message(
        id=update_id,
        date=DATE,
        chat=chat,
        from_user=stranger,
        text=Str(text),
        business_connection_id="conn-1",
    )
    return Update(update_id, business_message=received)


def deleted_business_messages(update_id: int) -> Update:
    chat = Chat(id=99, type=ChatType.PRIVATE)
    deleted = [
        Message(id=message_id, chat=chat, business_connection_id="conn-1") for message_id in (1, 2)
    ]
    return Update(update_id, deleted_business_messages=deleted)


def chat_member_joined(update_id: int) -> Update:
    chat = Chat(id=-1001234567890, type=ChatType.SUPERGROUP, title="Test Chat")
    new_member = User(id=99, is_bot=False, first_name="New")
    change = ChatMemberUpdated(
        chat=chat,
        from_user=User(id=42, is_bot=False, first_name="Tester"),
        date=DATE,
        old_chat_member=ChatMember(status=ChatMemberStatus.LEFT, user=new_member),
        new_chat_member=ChatMember(status=ChatMemberStatus.MEMBER, user=new_member),
    )
    return Update(update_id, chat_member=change)
