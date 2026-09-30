import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import respx
from pyfederation.enums import IncidentType
from pyrogram import Client

from spb.classes import report_submission_service
from spb.classes.federation_service import FederationService
from spb.objects.context.handler_context import HandlerContext
from spb.objects.context.report_context import ReportContext
from tests.support import contexts, payloads

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"
CHAT = -1001234567890
REPORT = "0192aaaa-0000-7000-8000-000000000004"

OPERATOR = {
    "uuid": "0192aaaa-0000-7000-8000-000000000001",
    "name": "bot",
    "disabled": False,
    "client_permissions": True,
    "management_permissions": False,
    "operator_permissions": False,
    "auto_assign": False,
    "created": 1_700_000_000,
    "updated": 1_700_000_000,
}

SUBMISSION = {
    "report": {
        "uuid": REPORT,
        "submitting_operator": OPERATOR["uuid"],
        "automated": False,
        "incident_type": "OTHER",
        "opened": True,
        "created": 1_700_000_000,
    },
    "evidence": [],
}


class RecordingClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})


def session(incident_type: IncidentType = IncidentType.OTHER, **changes: Any) -> ReportContext:
    return ReportContext(
        hash="",
        reporter_id=42,
        chat_id=CHAT,
        target_message_id=7,
        target_author_id=99,
        target_text="buy now",
        attachments=[],
        target_metadata={"text": "buy now"},
        incident_type=incident_type,
        ephemeral=True,
        **changes,
    )


@pytest.fixture
def client() -> RecordingClient:
    return RecordingClient()


def context_over(
    tmp_path: Path, federation: FederationService, client: RecordingClient
) -> HandlerContext:
    return contexts.template(tmp_path, federation, cast(Client, client))


@pytest.fixture
def unavailable(tmp_path: Path, client: RecordingClient) -> Iterator[HandlerContext]:
    context = context_over(tmp_path, FederationService.unavailable(), client)
    yield context
    context.database.close()


@pytest.fixture
async def authenticated(
    tmp_path: Path, client: RecordingClient, respx_mock: respx.MockRouter
) -> AsyncIterator[HandlerContext]:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(json=OPERATOR)
    federation = FederationService(ENDPOINT, "token")
    await federation.authenticate()
    context = context_over(tmp_path, federation, client)
    yield context
    context.database.close()


async def test_a_report_without_federation_is_answered_with_an_error(
    unavailable: HandlerContext, client: RecordingClient
) -> None:
    await report_submission_service.submit(unavailable, session(), None)

    (answer,) = client.sent
    assert answer["chat_id"] == CHAT
    assert answer["ephemeral_message_parameters"].receiver_user_id == 42
    assert "Federation" in answer["text"]
    assert answer["reply_markup"].inline_keyboard[0][0].callback_data == "report:dismiss"


async def test_an_operator_reference_is_filed_without_a_summary(
    authenticated: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(f"{ENDPOINT}/entities").respond(json="entity-uuid")
    reports = respx_mock.post(f"{ENDPOINT}/reports").respond(json=SUBMISSION)

    uuid = await report_submission_service.submit_operator_reference(
        authenticated, "operator-token", session()
    )

    assert uuid == REPORT
    assert client.sent == []
    assert reports.calls.last.request.headers["Authorization"] == "Bearer operator-token"
    evidence = json.loads(reports.calls.last.request.content)["evidence"]
    assert evidence["tag"] == "admin_report"
    assert evidence["metadata"]["chat_id"] == CHAT
    assert evidence["metadata"]["text"] == "buy now"


async def test_illegal_content_is_not_reported_to_a_server_that_declines_it(
    authenticated: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{ENDPOINT}/info").respond(
        json=payloads.server_information(allow_illegal_content=False)
    )
    reports = respx_mock.post(f"{ENDPOINT}/reports").respond(json=SUBMISSION)

    await report_submission_service.submit(
        authenticated, session(IncidentType.ILLEGAL_CONTENT), None
    )

    assert not reports.called
    (answer,) = client.sent
    assert "does not accept reports of illegal content" in answer["text"]


async def test_a_members_report_is_summarised_back_to_them(
    authenticated: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(f"{ENDPOINT}/entities").respond(json="entity-uuid")
    respx_mock.post(f"{ENDPOINT}/reports").respond(json=SUBMISSION)

    await report_submission_service.submit(authenticated, session(), "spam")

    (summary,) = client.sent
    assert REPORT in summary["text"]
    assert "user_report" in summary["text"]
