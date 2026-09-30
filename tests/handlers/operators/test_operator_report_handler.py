from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
import respx
from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.types import CallbackQuery, Chat, Message
from pyrogram.types.messages_and_media.message import Str

from spb.classes.federation_service import FederationService
from spb.handlers.operators.operator_report_handler import OperatorReportHandler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.operator_identity import OperatorIdentity
from spb.objects.update import Update
from tests.support import contexts, updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"
REPORT = "0192aaaa-0000-7000-8000-000000000004"


@pytest.fixture
def context(tmp_path: Path, client: RecordingClient) -> Iterator[HandlerContext]:
    context = contexts.template(tmp_path, FederationService(ENDPOINT), cast(Client, client))
    context.managers.operators.save_operator(42, OperatorIdentity("operator-uuid", "token"))
    yield context
    context.database.close()


def press(data: str) -> Update:
    notification = Message(
        id=3,
        date=updates.DATE,
        chat=Chat(id=42, type=ChatType.PRIVATE),
        text=Str(f"#REPORT_ASSIGNED\n\nReport ID: {REPORT}"),
    )
    query = CallbackQuery(
        id="cb", from_user=updates.TESTER, chat_instance="i", message=notification, data=data
    )
    return Update(1, callback_query=query)


def report(opened: bool) -> dict[str, object]:
    return {
        "uuid": REPORT,
        "submitting_operator": "operator-uuid",
        "automated": False,
        "incident_type": "SPAM",
        "opened": opened,
        "created": 1_700_000_000,
    }


async def test_a_report_is_closed_once_with_its_classification(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{ENDPOINT}/reports/{REPORT}").respond(json=report(True))
    close = respx_mock.patch(f"{ENDPOINT}/reports/{REPORT}/close").respond(json=True)
    handler = OperatorReportHandler()

    await handler.handle(context.with_update(press(f"opr:{REPORT}:MALICIOUS")))
    await handler.handle(context.with_update(press(f"opr:{REPORT}:MALICIOUS")))

    assert close.call_count == 1
    assert b"MALICIOUS" in close.calls.last.request.content
    assert len(client.named("send_message")) == 1


async def test_a_rejected_close_can_be_tried_again(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{ENDPOINT}/reports/{REPORT}").respond(json=report(True))
    close = respx_mock.patch(f"{ENDPOINT}/reports/{REPORT}/close").respond(403, json={})
    handler = OperatorReportHandler()

    await handler.handle(context.with_update(press(f"opr:{REPORT}:close")))
    await handler.handle(context.with_update(press(f"opr:{REPORT}:close")))

    assert close.call_count == 2
    assert client.named("send_message") == []


@pytest.mark.parametrize(
    ("status_code", "body", "alert"),
    [
        (200, report(False), "This report has already been closed."),
        (
            404,
            {"message": "Report not found"},
            "This report no longer exists on the Federation server.",
        ),
    ],
)
async def test_a_report_that_is_no_longer_open_only_loses_its_buttons(
    context: HandlerContext,
    client: RecordingClient,
    respx_mock: respx.MockRouter,
    status_code: int,
    body: dict[str, object],
    alert: str,
) -> None:
    respx_mock.get(f"{ENDPOINT}/reports/{REPORT}").respond(status_code, json=body)
    close = respx_mock.patch(f"{ENDPOINT}/reports/{REPORT}/close").respond(json=True)

    await OperatorReportHandler().handle(context.with_update(press(f"opr:{REPORT}:close")))

    assert not close.called
    assert len(client.named("edit_message_reply_markup")) == 1
    assert client.named("answer_callback_query")[0].kwargs["text"] == alert
