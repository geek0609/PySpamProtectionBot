from typing import Any, cast

import pytest
import respx
from pyfederation.records import ReportRecord
from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.types import Chat, InlineKeyboardMarkup, Message

from spb.classes.federation_service import FederationService
from spb.classes.federation_web_links import FederationWebLinks
from spb.classes.notifications.report_notification_sender import ReportNotificationSender
from spb.objects.context.handler_context import HandlerContext
from tests.support import updates

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"
OPERATOR_USER = 42
REPORT = "0192aaaa-0000-7000-8000-000000000004"
EVIDENCE = "0192aaaa-0000-7000-8000-000000000005"


class RecordingClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Message:
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        chat = Chat(id=chat_id, type=ChatType.PRIVATE)
        return Message(id=len(self.sent), date=updates.DATE, chat=chat)


def report() -> ReportRecord:
    return ReportRecord.model_validate(
        {
            "uuid": REPORT,
            "submitting_operator": "operator-uuid",
            "automated": False,
            "incident_type": "ILLEGAL_CONTENT",
            "opened": True,
            "message": "spam <everywhere>",
            "created": 1_700_000_000,
        }
    )


def callback_data(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def test_the_report_is_followed_by_its_evidence(
    respx_mock: respx.MockRouter, template: HandlerContext
) -> None:
    evidence = {
        "uuid": EVIDENCE,
        "entity": "entity-uuid",
        "operator": "operator-uuid",
        "confidential": False,
        "automated": False,
        "created": 1_700_000_000,
        "updated": 0,
    }
    respx_mock.get(url__startswith=f"{ENDPOINT}/reports/{REPORT}/evidence").respond(json=[evidence])
    respx_mock.get(url__startswith=f"{ENDPOINT}/entities/").respond(404, json={})
    respx_mock.get(url__startswith=f"{ENDPOINT}/evidence/{EVIDENCE}/attachments").respond(json=[])
    client = RecordingClient()
    sender = ReportNotificationSender(
        cast(Client, client),
        FederationService(ENDPOINT),
        template.managers,
        template.languages,
        FederationWebLinks(None),
    )

    await sender.send(OPERATOR_USER, report())

    notification, evidence_info = client.sent
    assert notification["text"].startswith("<b>#REPORT_ASSIGNED</b>")
    assert "ILLEGAL CONTENT" in notification["text"]
    assert "spam &lt;everywhere&gt;" in notification["text"]
    assert f"<code>{EVIDENCE}</code>" in notification["text"]
    assert callback_data(notification["reply_markup"]) == [
        f"opr:{REPORT}:close",
        f"opr:{REPORT}:NORMAL",
        f"opr:{REPORT}:SUSPICIOUS",
        f"opr:{REPORT}:MALICIOUS",
    ]
    assert evidence_info["reply_parameters"].message_id == 1
    assert EVIDENCE in evidence_info["text"]
