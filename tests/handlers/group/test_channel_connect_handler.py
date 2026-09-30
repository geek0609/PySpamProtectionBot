import pytest
from pyrogram.enums import ChatType
from pyrogram.types import Chat, Message
from pyrogram.types.messages_and_media.message import Str

from spb.handlers.group.channel_connect_handler import ChannelConnectHandler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.update import Update
from tests.support import updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

PROTECTED = -1001234567890
CHANNEL = Chat(id=-1009876543210, type=ChatType.CHANNEL, title="Notifications")
CODE = 1234567890


def post(text: str) -> Update:
    message = Message(id=5, date=updates.DATE, chat=CHANNEL, text=Str(text))
    return Update(1, channel_post=message)


@pytest.fixture
def linking(context: HandlerContext) -> HandlerContext:
    configurations = context.managers.chat_configurations
    configurations.enable_chat_configuration(PROTECTED)
    configurations.set_channel_link_verification_code(PROTECTED, CODE)
    return context


def test_only_connect_posts_are_accepted(context: HandlerContext) -> None:
    handler = ChannelConnectHandler()

    assert handler.accepts(context.with_update(post(f"/connect {CODE}")))
    assert handler.accepts(context.with_update(post("/connect@testbot 1")))
    assert not handler.accepts(context.with_update(post("/connection")))
    assert not handler.accepts(context.with_update(post("hello")))


async def test_a_channel_is_linked_with_its_verification_code(
    linking: HandlerContext, client: RecordingClient
) -> None:
    await ChannelConnectHandler().handle(linking.with_update(post(f"/connect {CODE}")))

    configuration = linking.managers.chat_configurations.resolve(PROTECTED)
    assert configuration.channel_link_id == CHANNEL.id
    assert configuration.channel_link_verification_code is None
    assert [call.method for call in client.calls] == ["send_message", "delete_messages"]


async def test_an_unknown_code_is_refused(linking: HandlerContext, client: RecordingClient) -> None:
    await ChannelConnectHandler().handle(linking.with_update(post("/connect 42")))

    assert linking.managers.chat_configurations.resolve(PROTECTED).channel_link_id is None
    (answer,) = client.named("send_message")
    assert answer.kwargs["text"] == linking.languages.get(
        linking.languages.default_language, "channel_connect", "unknown_id"
    )
