import pytest
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import ChatAdministratorRights, ChatMember, User

from spb.handlers.group.settings_handler import SettingsHandler
from spb.objects.context.handler_context import HandlerContext
from tests.support import contexts, updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio


BOT = User(id=contexts.BOT.id, is_bot=True, first_name="Test Bot")


def administrator(user: User) -> ChatMember:
    """An administrator who can change the group's information."""
    rights = ChatAdministratorRights(can_change_info=True)
    return ChatMember(status=ChatMemberStatus.ADMINISTRATOR, user=user, privileges=rights)


async def test_group_settings_tell_members_who_cannot_change_info(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond("get_chat_members", [administrator(BOT)])

    await SettingsHandler().handle(context.with_update(updates.group_message(1, "/settings")))

    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    assert sent.kwargs["text"] == context.languages.get(lang, "settings", "admin_required")
    assert sent.kwargs["ephemeral_message_parameters"].receiver_user_id == updates.TESTER.id


async def test_group_settings_sent_on_behalf_of_a_chat_get_a_regular_reply(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond("get_chat_members", [administrator(BOT)])
    command = updates.group_message(1, "/settings")
    assert command.message is not None
    command.message.sender_chat = command.message.chat

    await SettingsHandler().handle(context.with_update(command))

    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    assert sent.kwargs["text"] == context.languages.get(lang, "settings", "anonymous_sender")
    assert sent.kwargs["reply_parameters"].message_id == 1
    assert "ephemeral_message_parameters" not in sent.kwargs


async def test_group_settings_need_the_bot_to_change_info(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond("get_chat_members", [administrator(updates.TESTER)])

    await SettingsHandler().handle(context.with_update(updates.group_message(1, "/settings")))

    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    assert sent.kwargs["text"] == context.languages.get(lang, "settings", "bot_admin_required")
    assert sent.kwargs["reply_parameters"].message_id == 1
    assert sent.kwargs["ephemeral_message_parameters"].receiver_user_id == updates.TESTER.id


async def test_group_settings_open_the_menu_for_the_caller_only(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond("get_chat_members", [administrator(updates.TESTER), administrator(BOT)])

    await SettingsHandler().handle(context.with_update(updates.group_message(1, "/settings")))

    (sent,) = client.named("send_message")
    assert sent.kwargs["chat_id"] == -1001234567890
    assert sent.kwargs["ephemeral_message_parameters"].receiver_user_id == updates.TESTER.id


async def test_private_settings_without_secretary_mode_say_so(
    context: HandlerContext, client: RecordingClient
) -> None:
    await SettingsHandler().handle(context.with_update(updates.private_message(1, "/settings")))

    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    assert sent.kwargs["text"] == context.languages.get(lang, "settings", "secretary_not_enabled")


async def test_private_settings_open_the_secretary_menu(
    context: HandlerContext, client: RecordingClient
) -> None:
    context.managers.secretary_configurations.create_secretary_configuration(updates.TESTER.id)

    await SettingsHandler().handle(context.with_update(updates.private_message(1, "/settings")))

    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    assert sent.kwargs["text"].startswith(
        context.languages.get(lang, "secretary_settings", "title")
    )
