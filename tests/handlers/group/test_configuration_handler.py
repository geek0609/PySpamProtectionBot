"""Tests who may change a chat's settings through the configuration menu, and what the menu does.

The owner and every administrator with Change Group Information may change settings; enabling and
disabling the bot stays with the owner.
"""

import pytest
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import (
    CallbackQuery,
    Chat,
    ChatAdministratorRights,
    ChatMember,
    Message,
    User,
)
from pyrogram.types.messages_and_media.message import Str

from spb.enums import ConfigurationPage
from spb.handlers.group.configuration_handler import (
    ConfigurationHandler,
    can_change_info,
    is_chat_owner,
)
from spb.objects.context.configuration_context import ConfigurationContext
from spb.objects.context.handler_context import HandlerContext
from spb.objects.update import Update
from tests.support import updates
from tests.support.recording_client import RecordingClient

CHAT = -1001234567890
OWNER = User(id=42, is_bot=False, first_name="Owner")


def member(status: ChatMemberStatus, can_change: bool) -> ChatMember:
    rights = ChatAdministratorRights(
        can_delete_messages=True, can_restrict_members=True, can_change_info=can_change
    )
    return ChatMember(
        status=status, user=User(id=7, is_bot=False, first_name="Member"), privileges=rights
    )


def test_an_administrator_with_change_info_configures_but_does_not_toggle() -> None:
    administrator = member(ChatMemberStatus.ADMINISTRATOR, True)

    assert can_change_info(administrator)
    assert not is_chat_owner(administrator)


def test_an_administrator_without_change_info_may_not_change_settings() -> None:
    assert not can_change_info(member(ChatMemberStatus.ADMINISTRATOR, False))


def test_the_owner_may_do_both() -> None:
    owner = member(ChatMemberStatus.OWNER, True)

    assert can_change_info(owner)
    assert is_chat_owner(owner)


def test_a_regular_member_or_a_failed_lookup_may_do_neither() -> None:
    assert not can_change_info(member(ChatMemberStatus.MEMBER, False))
    assert not can_change_info(None)


@pytest.fixture
def session(context: HandlerContext) -> ConfigurationContext:
    return context.sessions.configuration.create(OWNER.id, CHAT)


def press(
    context: HandlerContext, session: ConfigurationContext, action: str, presser: User = OWNER
) -> HandlerContext:
    menu = Message(
        id=3, date=updates.DATE, chat=Chat(id=42, type=ChatType.PRIVATE), text=Str("menu")
    )
    query = CallbackQuery(
        id="cb",
        from_user=presser,
        chat_instance="instance",
        message=menu,
        data=f"cfg:{session.hash}:{action}",
    )
    return context.with_update(Update(1, callback_query=query))


@pytest.mark.anyio
async def test_the_owner_enables_the_bot_from_the_menu(
    context: HandlerContext, session: ConfigurationContext, client: RecordingClient
) -> None:
    client.respond("get_chat_member", member(ChatMemberStatus.OWNER, True))

    await ConfigurationHandler().handle(press(context, session, "enable"))

    assert context.managers.chat_configurations.resolve(CHAT).enabled
    (edit,) = client.named("edit_message_text")
    assert edit.kwargs["message_id"] == 3
    (answer,) = client.named("answer_callback_query")
    assert answer.kwargs["text"] == context.languages.get(
        context.languages.default_language, "configuration", "toast_enabled"
    )


@pytest.mark.anyio
async def test_an_administrator_cannot_enable_the_bot(
    context: HandlerContext, session: ConfigurationContext, client: RecordingClient
) -> None:
    client.respond("get_chat_member", member(ChatMemberStatus.ADMINISTRATOR, True))

    await ConfigurationHandler().handle(press(context, session, "enable"))

    assert not context.managers.chat_configurations.resolve(CHAT).enabled
    (alert,) = client.named("answer_callback_query")
    assert alert.kwargs["show_alert"] is True


@pytest.mark.anyio
async def test_someone_elses_menu_is_left_to_its_owner(
    context: HandlerContext, session: ConfigurationContext, client: RecordingClient
) -> None:
    stranger = User(id=99, is_bot=False, first_name="Stranger")

    await ConfigurationHandler().handle(press(context, session, "configure", stranger))

    assert [call.method for call in client.calls] == ["answer_callback_query"]
    current = context.sessions.configuration.find(session.hash)
    assert current is not None
    assert current.page is ConfigurationPage.MAIN


@pytest.mark.anyio
async def test_blocking_ads_and_trackers_is_toggled_on_the_scanning_page(
    context: HandlerContext, session: ConfigurationContext, client: RecordingClient
) -> None:
    client.respond("get_chat_member", member(ChatMemberStatus.OWNER, True))
    context.managers.chat_configurations.enable_chat_configuration(CHAT)

    await ConfigurationHandler().handle(press(context, session, "toggle_ads_trackers"))

    assert not context.managers.chat_configurations.resolve(CHAT).block_ads_trackers
    (edit,) = client.named("edit_message_text")
    lm = context.languages
    enable = lm.get(lm.default_language, "configuration", "scanning.enable_ads_trackers")
    buttons = [button.text for row in edit.kwargs["reply_markup"].inline_keyboard for button in row]
    assert any(enable in text for text in buttons)
