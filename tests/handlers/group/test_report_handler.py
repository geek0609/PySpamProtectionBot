from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
import respx
from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.types import CallbackQuery, Chat, Message, User
from pyrogram.types.messages_and_media.message import Str

from spb.classes.federation_service import FederationService
from spb.enums import ReportPage
from spb.handlers.group.report_handler import ReportHandler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.update import Update
from tests.support import contexts, payloads, updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"
GROUP = Chat(id=-1001234567890, type=ChatType.SUPERGROUP, title="Group")
SPAMMER = User(id=99, is_bot=False, first_name="Spammer")


@pytest.fixture
async def context(
    tmp_path: Path, client: RecordingClient, respx_mock: respx.MockRouter
) -> AsyncIterator[HandlerContext]:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(
        json={
            "uuid": "0192aaaa-0000-7000-8000-000000000001",
            "name": "bot",
            "disabled": False,
            "permissions": {"client": True, "management": False, "operator": False},
            "configuration": {
                "auto_assign_reports": False,
                "auto_assign_automated_reports": False,
                "is_bot": True,
            },
            "created": 1_700_000_000,
            "updated": 1_700_000_000,
        }
    )
    respx_mock.get(f"{ENDPOINT}/info").respond(
        json=payloads.server_information(allow_illegal_content=False)
    )
    federation = FederationService(ENDPOINT, "token")
    await federation.authenticate()
    context = contexts.template(tmp_path, federation, cast(Client, client))
    context.managers.chat_configurations.enable_chat_configuration(GROUP.id)
    yield context
    context.database.close()


def report_command() -> Update:
    target = Message(id=50, date=updates.DATE, chat=GROUP, from_user=SPAMMER, text=Str("buy now"))
    command = Message(
        id=51,
        date=updates.DATE,
        chat=GROUP,
        from_user=updates.TESTER,
        text=Str("/report"),
        reply_to_message_id=50,
        reply_to_message=target,
    )
    return Update(1, message=command)


def press(data: str, prompt_id: int) -> Update:
    prompt = Message(id=prompt_id, date=updates.DATE, chat=GROUP, ephemeral_message_id=prompt_id)
    query = CallbackQuery(
        id="cb", from_user=updates.TESTER, chat_instance="i", message=prompt, data=data
    )
    return Update(2, callback_query=query)


async def test_report_commands_and_callbacks_are_accepted(context: HandlerContext) -> None:
    handler = ReportHandler()

    assert handler.accepts(context.with_update(report_command()))
    assert handler.accepts(context.with_update(updates.callback_query(1, "report:dismiss")))
    assert not handler.accepts(context.with_update(updates.group_message(1, "hello")))


async def test_a_report_opens_an_ephemeral_dialog_that_advances_on_a_type(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond(
        "send_message",
        Message(id=70, date=updates.DATE, chat=GROUP, ephemeral_message_id=70),
    )

    await ReportHandler().handle(context.with_update(report_command()))

    (prompt,) = client.named("send_message")
    assert prompt.kwargs["ephemeral_message_parameters"].receiver_user_id == updates.TESTER.id
    offered = [row[0].callback_data for row in prompt.kwargs["reply_markup"].inline_keyboard]
    assert not any(data.endswith(":type:ILLEGAL_CONTENT") for data in offered)
    session = context.sessions.report.find_by_prompt_message_id(GROUP.id, 70)
    assert session is not None
    assert session.target_author_id == SPAMMER.id

    await ReportHandler().handle(context.with_update(press(f"report:{session.hash}:type:SPAM", 70)))

    advanced = context.sessions.report.find(session.hash)
    assert advanced is not None
    assert advanced.page is ReportPage.COMMENT
    assert len(client.named("edit_ephemeral_message_text")) == 1


async def test_a_report_of_ones_own_message_is_refused(
    context: HandlerContext, client: RecordingClient
) -> None:
    target = Message(
        id=50, date=updates.DATE, chat=GROUP, from_user=updates.TESTER, text=Str("mine")
    )
    command = Message(
        id=51,
        date=updates.DATE,
        chat=GROUP,
        from_user=updates.TESTER,
        text=Str("/report"),
        reply_to_message_id=50,
        reply_to_message=target,
    )

    await ReportHandler().handle(context.with_update(Update(1, message=command)))

    (answer,) = client.named("send_message")
    assert answer.kwargs["text"] == context.languages.get(
        context.languages.default_language, "report", "self_report"
    )
