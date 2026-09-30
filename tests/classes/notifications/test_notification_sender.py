from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.errors import ChatForwardsRestricted
from pyrogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message

from spb.classes.notifications import notification_sender
from spb.objects.admin_info import AdminInfo
from spb.objects.context.handler_context import HandlerContext
from tests.support import contexts, updates

pytestmark = pytest.mark.anyio

CHAT = -1001234567890
CHANNEL = -1009876543210
DELETER = 1
BANNER = 2


class RecordingClient:
    """Stands in for the Telegram client, recording what is sent and refusing forwards to the
    chats named in restricted."""

    def __init__(self, restricted: set[int]) -> None:
        self.restricted = restricted
        self.sent: list[dict[str, Any]] = []
        self.next_id = 100

    async def forward_messages(
        self, chat_id: int, from_chat_id: int, message_ids: list[int], **_: Any
    ) -> list[Message]:
        if chat_id in self.restricted:
            raise ChatForwardsRestricted
        return [self._message(chat_id) for _ in message_ids]

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Message:
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return self._message(chat_id)

    def _message(self, chat_id: int) -> Message:
        self.next_id += 1
        return Message(
            id=self.next_id, date=updates.DATE, chat=Chat(id=chat_id, type=ChatType.PRIVATE)
        )


@pytest.fixture
def client() -> RecordingClient:
    return RecordingClient({BANNER})


@pytest.fixture
def context(tmp_path: Path, client: RecordingClient) -> Iterator[HandlerContext]:
    context = contexts.template(tmp_path, client=cast(Client, client))
    configurations = context.managers.chat_configurations
    configurations.enable_chat_configuration(CHAT)
    configurations.link_channel(CHAT, CHANNEL, None)
    context.chat_admins.put(
        CHAT,
        [
            AdminInfo(contexts.BOT.id, False, True, True, False),
            AdminInfo(DELETER, False, True, False, False),
            AdminInfo(BANNER, False, False, True, False),
        ],
    )
    yield context
    context.database.close()


def buttons(markup: InlineKeyboardMarkup | None) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row] if markup else []


async def test_each_moderator_gets_the_buttons_they_and_the_bot_may_use(
    context: HandlerContext, client: RecordingClient
) -> None:
    link = InlineKeyboardButton("View Report", url="https://audit.example/report")

    targets = await notification_sender.notify_report_submitted(
        context, CHAT, "report", 55, 99, True, link
    )

    by_chat = {sent["chat_id"]: sent for sent in client.sent}
    assert buttons(by_chat[DELETER]["reply_markup"]) == ["Delete Message", "View Report"]
    assert buttons(by_chat[BANNER]["reply_markup"]) == [
        "Delete + Mute 1h",
        "Delete + Ban",
        "View Report",
    ]
    assert buttons(by_chat[CHANNEL]["reply_markup"]) == ["View Report"]
    # A chat the report cannot be forwarded to still gets the notification, without the reply.
    assert by_chat[BANNER]["reply_parameters"] is None
    assert by_chat[DELETER]["reply_parameters"] is not None
    assert [target.chat_id for target in targets] == [DELETER, BANNER]
    assert all(buttons(target.retained_markup) == ["View Report"] for target in targets)


async def test_notifications_reach_the_linked_chat_and_every_moderator_but_the_bot(
    context: HandlerContext, client: RecordingClient
) -> None:
    await notification_sender.notify(context, CHAT, "<b>event</b>")
    await notification_sender.notify(context, CHAT, " ")

    assert [sent["chat_id"] for sent in client.sent] == [CHANNEL, DELETER, BANNER]
