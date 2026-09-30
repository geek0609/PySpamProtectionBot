import pytest
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import (
    ChatAdministratorRights,
    ChatMember,
    InlineKeyboardMarkup,
    MessageOriginUser,
    User,
)

from spb.handlers.start_handler import StartHandler
from spb.objects.context.handler_context import HandlerContext
from tests.support import contexts, updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

BOT = User(id=contexts.BOT.id, is_bot=True, first_name="Test Bot")


def administrator(user: User) -> ChatMember:
    """An administrator who can change the group's information."""
    rights = ChatAdministratorRights(can_change_info=True)
    return ChatMember(status=ChatMemberStatus.ADMINISTRATOR, user=user, privileges=rights)


def urls(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.url for row in markup.inline_keyboard for button in row if button.url]


def test_a_forwarded_start_is_not_a_command(context: HandlerContext) -> None:
    update = updates.private_message(1, "/start")
    assert update.message is not None
    update.message.forward_origin = MessageOriginUser(
        type="user", date=updates.DATE, sender_user=updates.TESTER
    )

    assert not StartHandler().accepts(context.with_update(update))
    assert StartHandler().accepts(context.with_update(updates.private_message(1, "/start")))


# Private chats


async def test_private_start_shows_the_start_screen(
    context: HandlerContext, client: RecordingClient
) -> None:
    await StartHandler().handle(context.with_update(updates.private_message(1, "/start")))

    (sent,) = client.named("send_message")
    assert sent.kwargs["text"].startswith(f"<b>{context.bot_name}</b>")
    assert urls(sent.kwargs["reply_markup"]) == [
        f"https://t.me/{context.bot_username}?startgroup=add"
    ]


async def test_a_configure_link_for_somebody_else_has_expired(
    context: HandlerContext, client: RecordingClient
) -> None:
    session = context.sessions.configuration.create(7, -1001234567890)

    await StartHandler().handle(
        context.with_update(updates.private_message(1, f"/start {session.hash}"))
    )

    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    assert sent.kwargs["text"] == context.languages.get(lang, "start", "session_expired_private")


async def test_a_configure_link_opens_the_configuration_menu(
    context: HandlerContext, client: RecordingClient
) -> None:
    session = context.sessions.configuration.create(updates.TESTER.id, -1001234567890)

    await StartHandler().handle(
        context.with_update(updates.private_message(1, f"/start {session.hash}"))
    )

    (sent,) = client.named("send_message")
    assert sent.kwargs["chat_id"] == updates.TESTER.id
    assert sent.kwargs["reply_markup"] is not None


# Groups


async def test_members_see_only_the_bots_information(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond("get_chat_members", [administrator(BOT)])

    await StartHandler().handle(context.with_update(updates.group_message(1, "/start")))

    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    header = context.languages.get(lang, "start", "getting_started_header")
    assert header not in sent.kwargs["text"]
    assert sent.kwargs["reply_markup"] is None


async def test_administrators_get_a_configure_link(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond("get_chat_members", [administrator(updates.TESTER), administrator(BOT)])

    await StartHandler().handle(context.with_update(updates.group_message(1, "/start")))

    (sent,) = client.named("send_message")
    (url,) = urls(sent.kwargs["reply_markup"])
    hash = url.rpartition("?start=")[2]
    session = context.sessions.configuration.find(hash)
    assert session is not None
    assert session.user_id == updates.TESTER.id


async def test_administrators_are_told_to_promote_the_bot_first(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond("get_chat_members", [administrator(updates.TESTER)])

    await StartHandler().handle(context.with_update(updates.group_message(1, "/start")))

    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    permissions = context.languages.get(lang, "start", "getting_started_group_permissions")
    assert permissions in sent.kwargs["text"]
    assert sent.kwargs["reply_markup"] is None


async def test_a_startgroup_link_asks_for_promotion(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond("get_chat_members", [])

    await StartHandler().handle(context.with_update(updates.group_message(1, "/start add")))

    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    assert sent.kwargs["text"] == context.languages.get(lang, "start", "startgroup_promote")


async def test_a_startgroup_link_stays_silent_after_the_join_was_announced(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond("get_chat_members", [])
    context.cache.put("bot-membership:-1001234567890", True)

    await StartHandler().handle(context.with_update(updates.group_message(1, "/start add")))

    assert client.named("send_message") == []
