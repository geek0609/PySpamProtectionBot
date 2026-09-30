import pytest
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import CallbackQuery, Chat, ChatAdministratorRights, ChatMember, Message, User
from pyrogram.types.messages_and_media.message import Str

from spb.classes.notifications.report_action_callback import Action, ReportActionCallback
from spb.handlers.group.report_action_handler import ReportActionHandler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.notification_target import NotificationTarget
from spb.objects.update import Update
from tests.support import contexts, updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

CHAT = -1001234567890
MODERATOR = User(id=7, is_bot=False, first_name="Moderator")
BUTTON = ReportActionCallback(CHAT, 55, 99, Action.BAN)


def press(data: str, presser: User = MODERATOR) -> Update:
    notification = Message(
        id=3, date=updates.DATE, chat=Chat(id=7, type=ChatType.PRIVATE), text=Str("#REPORT")
    )
    query = CallbackQuery(
        id="cb", from_user=presser, chat_instance="i", message=notification, data=data
    )
    return Update(1, callback_query=query)


@pytest.fixture
def moderated(context: HandlerContext, client: RecordingClient) -> HandlerContext:
    def administrator(user: User) -> ChatMember:
        rights = ChatAdministratorRights(can_delete_messages=True, can_restrict_members=True)
        return ChatMember(status=ChatMemberStatus.ADMINISTRATOR, user=user, privileges=rights)

    bot = User(id=contexts.BOT.id, is_bot=True, first_name="Test Bot")
    client.respond("get_chat_members", [administrator(bot), administrator(MODERATOR)])
    return context


def text(context: HandlerContext, key: str) -> str:
    return context.languages.get(context.languages.default_language, "report", key)


async def test_the_first_press_bans_and_clears_every_copy(
    moderated: HandlerContext, client: RecordingClient
) -> None:
    moderated.sessions.report_actions.track(CHAT, 55, [NotificationTarget(8, 30)])

    await ReportActionHandler().handle(moderated.with_update(press(BUTTON.data)))

    methods = [call.method for call in client.calls if call.method != "get_chat_members"]
    assert methods == [
        "delete_messages",
        "ban_chat_member",
        "edit_message_reply_markup",
        "edit_message_reply_markup",
        "answer_callback_query",
    ]
    assert client.named("answer_callback_query")[0].kwargs["text"] == text(
        moderated, "action_completed"
    )


async def test_a_second_press_is_told_it_was_already_done(
    moderated: HandlerContext, client: RecordingClient
) -> None:
    moderated.sessions.report_actions.claim(CHAT, 55)

    await ReportActionHandler().handle(moderated.with_update(press(BUTTON.data)))

    assert client.named("ban_chat_member") == []
    (answer,) = client.named("answer_callback_query")
    assert answer.kwargs["text"] == text(moderated, "already_processed")
