from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
import respx
from pyrogram import Client

from spb.classes.federation_service import FederationService
from spb.handlers.operators.link_handler import LinkHandler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.operator_identity import OperatorIdentity
from spb.objects.database.user_identity import UserIdentity
from tests.support import contexts, updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"


@pytest.fixture
def context(tmp_path: Path, client: RecordingClient) -> Iterator[HandlerContext]:
    context = contexts.template(tmp_path, FederationService(ENDPOINT), cast(Client, client))
    context.managers.operators.save_operator(42, OperatorIdentity("operator-uuid", "token"))
    yield context
    context.database.close()


def answer(client: RecordingClient) -> str:
    (sent,) = client.named("send_message")
    text: str = sent.kwargs["text"]
    return text


async def test_two_entities_are_linked_by_their_addresses(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    context.managers.users.save_user(UserIdentity(99, "spammer", "Spammer", None))
    link = respx_mock.patch(f"{ENDPOINT}/entities/99@telegram.org/relationship").respond(json=True)

    command = "/link @spammer 100 alternative"
    await LinkHandler().handle(context.with_update(updates.private_message(1, command)))

    assert b"100@telegram.org" in link.calls.last.request.content
    lang = context.languages.default_language
    assert answer(client) == context.languages.get(
        lang, "link", "linked", "@spammer", "100", "alternative"
    )


async def test_an_unknown_relationship_is_refused(
    context: HandlerContext, client: RecordingClient
) -> None:
    await LinkHandler().handle(context.with_update(updates.private_message(1, "/link 1 2 sibling")))

    lang = context.languages.default_language
    assert answer(client) == context.languages.get(lang, "link", "invalid_type", "sibling")
    assert "<code>sibling</code>" in answer(client)
