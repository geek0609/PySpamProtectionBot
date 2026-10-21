from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
import respx
from pyrogram import Client

from spb.classes.federation_service import FederationService
from spb.handlers.operators.authentication_handler import AuthenticationHandler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.operator_identity import OperatorIdentity
from tests.support import contexts, updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"
OPERATOR_UUID = "0192aaaa-0000-7000-8000-000000000003"
OPERATOR = {
    "uuid": OPERATOR_UUID,
    "name": "alice",
    "disabled": False,
    "permissions": {"client": True, "management": False, "operator": True},
    "configuration": {
        "auto_assign_reports": False,
        "auto_assign_automated_reports": False,
        "is_bot": False,
    },
    "created": 1_700_000_000,
    "updated": 1_700_000_000,
}


@pytest.fixture
def context(tmp_path: Path, client: RecordingClient) -> Iterator[HandlerContext]:
    context = contexts.template(tmp_path, FederationService(ENDPOINT), cast(Client, client))
    yield context
    context.database.close()


def answer(client: RecordingClient) -> str:
    (sent,) = client.named("send_message")
    text: str = sent.kwargs["text"]
    return text


async def test_an_accepted_token_is_stored(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(json=OPERATOR)

    await AuthenticationHandler().handle(
        context.with_update(updates.private_message(1, "/auth secret"))
    )

    assert context.managers.operators.get_operator(42) == OperatorIdentity(OPERATOR_UUID, "secret")
    assert "alice" in answer(client)


async def test_a_token_sent_in_a_group_is_deleted_and_not_used(
    context: HandlerContext, client: RecordingClient
) -> None:
    await AuthenticationHandler().handle(
        context.with_update(updates.group_message(1, "/auth leaked"))
    )

    assert context.managers.operators.get_operator(42) is None
    (deleted,) = client.named("delete_messages")
    assert deleted.args == (-1001234567890, 1)
    assert answer(client) == context.languages.get(
        context.languages.default_language, "authentication", "token_deleted"
    )


async def test_a_token_that_cannot_be_deleted_is_reported_exposed(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond("delete_messages", 0)

    await AuthenticationHandler().handle(
        context.with_update(updates.group_message(1, "/auth leaked"))
    )

    assert context.managers.operators.get_operator(42) is None
    assert answer(client) == context.languages.get(
        context.languages.default_language, "authentication", "token_not_deleted"
    )


async def test_auth_without_a_token_in_a_group_points_to_the_private_chat(
    context: HandlerContext, client: RecordingClient
) -> None:
    await AuthenticationHandler().handle(context.with_update(updates.group_message(1, "/auth")))

    assert not client.named("delete_messages")
    assert answer(client) == context.languages.get(
        context.languages.default_language, "authentication", "private_only"
    )


async def test_a_rejected_token_is_not_stored(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(401, json={"error": "unauthorized"})

    await AuthenticationHandler().handle(
        context.with_update(updates.private_message(1, "/auth wrong"))
    )

    assert context.managers.operators.get_operator(42) is None
    assert answer(client) == context.languages.get(
        context.languages.default_language, "authentication", "rejected"
    )


async def test_signing_out_forgets_the_credential(
    context: HandlerContext, client: RecordingClient
) -> None:
    context.managers.operators.save_operator(42, OperatorIdentity(OPERATOR_UUID, "t"))

    await AuthenticationHandler().handle(context.with_update(updates.private_message(1, "/deauth")))

    assert context.managers.operators.get_operator(42) is None
