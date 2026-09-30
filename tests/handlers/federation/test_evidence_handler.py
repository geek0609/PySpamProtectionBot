import pytest

from spb.handlers.federation.evidence_handler import EvidenceHandler
from spb.objects.context.handler_context import HandlerContext
from tests.support import updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("text", ["/evidence", "/evidence not-a-uuid", "/evidence a b"])
async def test_anything_but_one_uuid_is_answered_with_the_usage(
    context: HandlerContext, client: RecordingClient, text: str
) -> None:
    await EvidenceHandler().handle(context.with_update(updates.private_message(1, text)))

    (answer,) = client.named("send_message")
    assert answer.kwargs["text"] == context.languages.get(
        context.languages.default_language, "evidence", "usage"
    )


async def test_a_lookup_without_federation_reports_the_failure(
    context: HandlerContext, client: RecordingClient
) -> None:
    uuid = "0192aaaa-0000-7000-8000-000000000005"

    await EvidenceHandler().handle(
        context.with_update(updates.private_message(1, f"/evidence {uuid}"))
    )

    (answer,) = client.named("send_message")
    assert answer.kwargs["text"] == context.languages.get(
        context.languages.default_language, "evidence", "query_error"
    )
