"""Tests that each administrator check asks for the permission it names.

The cached list holds every administrator. The settings menu needs Change Group Information, while
moderation needs the right to delete messages or restrict members, and neither implies the other.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import cast

import pytest
from pyrogram import Client
from pyrogram.enums import ChatMembersFilter, ChatMemberStatus, ChatType
from pyrogram.types import Chat, ChatAdministratorRights, ChatMember, Message, User

from spb.classes.cache import LoadFailedException
from spb.classes.handler import Handler
from spb.objects.admin_info import AdminInfo
from spb.objects.context.handler_context import HandlerContext
from tests.support import contexts, updates

CHAT = -1001234567890
INFO_ONLY = 1
DELETE_ONLY = 2
OWNER = 3


def context_with(
    template: HandlerContext, bot_can_change_info: bool, bot_can_delete: bool
) -> HandlerContext:
    """Caches an administrator list for CHAT holding the bot and the test's administrators."""
    template.chat_admins.put(
        CHAT,
        [
            AdminInfo(contexts.BOT.id, False, bot_can_delete, False, bot_can_change_info),
            AdminInfo(INFO_ONLY, False, False, False, True),
            AdminInfo(DELETE_ONLY, False, True, False, False),
            AdminInfo(OWNER, True, True, True, True),
        ],
    )
    return template


def message_from(user_id: int) -> Message:
    return Message(
        id=1,
        date=updates.DATE,
        chat=Chat(id=CHAT, type=ChatType.SUPERGROUP, title="Test Chat"),
        from_user=User(id=user_id, is_bot=False, first_name="Tester"),
    )


def test_a_bot_with_only_change_group_information_may_open_the_settings_menu(
    template: HandlerContext,
) -> None:
    # The permission the bot asks for is Change Group Information; Delete Messages and Ban Users
    # are optional.
    assert Handler.is_bot_change_information_administrator(
        context_with(template, True, False), CHAT
    )


def test_a_bot_without_change_group_information_may_not(template: HandlerContext) -> None:
    context = context_with(template, False, True)

    assert not Handler.is_bot_change_information_administrator(context, CHAT)


def test_the_settings_menu_follows_change_group_information(template: HandlerContext) -> None:
    context = context_with(template, True, True)

    assert Handler.is_author_change_information_administrator(context, message_from(INFO_ONLY))
    assert Handler.is_author_change_information_administrator(context, message_from(OWNER))
    assert not Handler.is_author_change_information_administrator(
        context, message_from(DELETE_ONLY)
    )


def test_moderation_follows_the_right_to_delete_or_restrict(template: HandlerContext) -> None:
    context = context_with(template, True, True)

    assert Handler.is_chat_administrator(context, CHAT, DELETE_ONLY)
    assert Handler.is_chat_administrator(context, CHAT, OWNER)
    assert not Handler.is_chat_administrator(context, CHAT, INFO_ONLY)


class AdministratorsClient:
    def __init__(self, members: list[ChatMember] | None) -> None:
        self.members = members

    async def get_chat_members(
        self, chat_id: int, filter: ChatMembersFilter
    ) -> AsyncIterator[ChatMember]:
        assert filter is ChatMembersFilter.ADMINISTRATORS
        if self.members is None:
            raise ConnectionError("offline")
        for member in self.members:
            yield member


@pytest.fixture
def context_over(tmp_path: Path) -> Iterator[Callable[[AdministratorsClient], HandlerContext]]:
    """Builds a context whose Telegram client is the given stand-in."""
    opened: list[HandlerContext] = []

    def build(client: AdministratorsClient) -> HandlerContext:
        opened.append(contexts.template(tmp_path, client=cast(Client, client)))
        return opened[-1]

    yield build
    for context in opened:
        context.database.close()


@pytest.mark.anyio
async def test_administrators_are_loaded_with_their_rights(
    context_over: Callable[[AdministratorsClient], HandlerContext],
) -> None:
    def user(user_id: int) -> User:
        return User(id=user_id, is_bot=False, first_name="Admin")

    rights = ChatAdministratorRights(can_delete_messages=True, can_change_info=False)
    client = AdministratorsClient(
        [
            ChatMember(status=ChatMemberStatus.OWNER, user=user(OWNER)),
            ChatMember(
                status=ChatMemberStatus.ADMINISTRATOR, user=user(DELETE_ONLY), privileges=rights
            ),
            # An administrator of a basic group, whose rights MTProto leaves unstated.
            ChatMember(status=ChatMemberStatus.ADMINISTRATOR, user=user(INFO_ONLY)),
        ]
    )

    administrators = await Handler.load_administrators(context_over(client), CHAT)

    assert administrators == [
        AdminInfo(OWNER, True, True, True, True),
        AdminInfo(DELETE_ONLY, False, True, False, False),
        AdminInfo(INFO_ONLY, False, True, True, True),
    ]


@pytest.mark.anyio
async def test_a_failed_refresh_keeps_the_cached_administrators(
    context_over: Callable[[AdministratorsClient], HandlerContext],
) -> None:
    context = context_over(AdministratorsClient(None))
    cached = [AdminInfo(OWNER, True, True, True, True)]
    context.chat_admins.put(CHAT, cached)

    with pytest.raises(LoadFailedException):
        await Handler.load_administrators(context, CHAT)
    await Handler.refresh_administrators(context, CHAT)

    assert context.chat_admins.get_if_present(CHAT) == cached
