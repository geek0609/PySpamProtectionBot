import pytest

from spb.handlers.ping_handler import PingHandler
from spb.objects.context.handler_context import HandlerContext
from tests.support import updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio


async def test_ping_reports_the_bot_online_and_federation_unconfigured(
    context: HandlerContext, client: RecordingClient
) -> None:
    await PingHandler().handle(context.with_update(updates.private_message(1, "/ping")))

    (sent,) = client.named("send_message")
    lm, lang = context.languages, context.languages.default_language
    text = sent.kwargs["text"]
    assert lm.get(lang, "ping", "bot_status", lm.get(lang, "ping", "status_online")) in text
    not_configured = lm.get(lang, "ping", "status_not_configured")
    assert lm.get(lang, "ping", "federation_status", not_configured) in text
    assert lm.get(lang, "ping", "operator_header") not in text
