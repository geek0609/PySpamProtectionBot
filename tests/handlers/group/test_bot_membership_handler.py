"""Tests which changes of the bot's own membership BotMembershipHandler announces."""

import pytest
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import Chat, ChatAdministratorRights, ChatMember, ChatMemberUpdated, User

from spb.handlers.group import bot_membership_handler
from spb.handlers.group.bot_membership_handler import BotMembershipHandler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.update import Update
from tests.support import contexts, updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

GROUP = -1004001234567
BOT = User(id=contexts.BOT.id, is_bot=True, first_name="Test Bot")
OWNER = User(id=42, is_bot=False, first_name="Owner")


def administrator(can_change_info: bool, can_delete: bool) -> ChatMember:
    rights = ChatAdministratorRights(
        can_manage_chat=True, can_delete_messages=can_delete, can_change_info=can_change_info
    )
    return ChatMember(status=ChatMemberStatus.ADMINISTRATOR, user=BOT, privileges=rights)


def left() -> ChatMember:
    return ChatMember(status=ChatMemberStatus.LEFT, user=BOT)


async def announces(context: HandlerContext, old: ChatMember, new: ChatMember) -> bool:
    """Runs the handler over a change of the bot's own membership and returns whether it announced
    anything."""
    change = ChatMemberUpdated(
        chat=Chat(id=GROUP, type=ChatType.SUPERGROUP, title="Test Chat"),
        from_user=OWNER,
        date=updates.DATE,
        old_chat_member=old,
        new_chat_member=new,
    )
    await BotMembershipHandler().handle(context.with_update(Update(1, my_chat_member=change)))
    return bot_membership_handler.claim_join(context, GROUP)


async def test_an_unrelated_permission_change_is_not_announced(context: HandlerContext) -> None:
    assert not await announces(context, administrator(True, True), administrator(True, False))


async def test_losing_the_configuration_permission_is_not_announced(
    context: HandlerContext,
) -> None:
    assert not await announces(context, administrator(True, True), administrator(False, True))


async def test_being_removed_from_the_group_is_not_announced(context: HandlerContext) -> None:
    assert not await announces(context, administrator(True, True), left())


async def test_joining_without_the_permission_asks_for_it_once(
    context: HandlerContext, client: RecordingClient
) -> None:
    assert await announces(context, left(), administrator(False, True))
    assert not bot_membership_handler.claim_join(context, GROUP)

    (sent,) = client.named("send_message")
    assert sent.kwargs["text"] == context.languages.get(
        context.languages.default_language, "bot_added", "permissions_required"
    )
