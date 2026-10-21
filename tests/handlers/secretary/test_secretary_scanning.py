"""Checks that the secretary's scanning toggle decides whether a message's content reaches
Federation, while the sender is queried either way.

A mocked server stands in for Federation and records every request it receives; it answers with an
error, which the secretary treats as an unavailable analysis.
"""

from pathlib import Path

import pytest
import respx

from spb.classes.federation_service import FederationService
from spb.enums import ScanningBehavior
from spb.handlers.secretary import secretary_message_handler
from spb.objects.database.secretary_configuration import SecretaryConfiguration
from tests.support import contexts, updates

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"
SECRET = "my bank PIN is 4921"
BEHAVIORS = [ScanningBehavior.PASSIVE, ScanningBehavior.STRICT]


async def recommend(
    tmp_path: Path, respx_mock: respx.MockRouter, configuration: SecretaryConfiguration
) -> list[str]:
    """Asks for a recommendation about a first-contact message, returning every request the
    server received."""
    route = respx_mock.route(host="127.0.0.1").respond(500)
    context = contexts.template(tmp_path, FederationService(ENDPOINT))
    message = updates.business_message(1, SECRET).business_message
    assert message is not None
    assert message.from_user is not None
    try:
        await secretary_message_handler.recommend(
            context, configuration, message, message.from_user
        )
    finally:
        context.database.close()
    return [f"{call.request.url}\n{call.request.content.decode()}" for call in route.calls]


@pytest.mark.parametrize("behavior", BEHAVIORS)
async def test_with_scanning_off_the_content_is_never_sent_but_the_sender_is_queried(
    tmp_path: Path, respx_mock: respx.MockRouter, behavior: ScanningBehavior
) -> None:
    configuration = SecretaryConfiguration(42, "conn", behavior, False, False)

    requests = await recommend(tmp_path, respx_mock, configuration)

    assert not any(SECRET in request for request in requests), requests
    assert any("/entities/99%40telegram.org/query" in request for request in requests), requests


@pytest.mark.parametrize("behavior", BEHAVIORS)
async def test_with_scanning_on_the_content_is_sent_for_analysis(
    tmp_path: Path, respx_mock: respx.MockRouter, behavior: ScanningBehavior
) -> None:
    configuration = SecretaryConfiguration(42, "conn", behavior, False, True)

    requests = await recommend(tmp_path, respx_mock, configuration)

    assert any(SECRET in request for request in requests), requests
