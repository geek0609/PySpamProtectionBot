"""Tests how BlacklistHandler reads its arguments and the entities it publishes before
blacklisting."""

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
import respx
from pyrogram import Client

from spb.classes.federation_service import FederationService
from spb.handlers.operators.blacklist_handler import BlacklistHandler, telegram_id
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.operator_identity import OperatorIdentity
from spb.objects.database.user_identity import UserIdentity
from tests.support import contexts, updates
from tests.support.recording_client import RecordingClient

ENDPOINT = "http://127.0.0.1:1"
REPORT = "0192aaaa-0000-7000-8000-000000000004"


def test_user_and_chat_addresses_yield_their_telegram_ids() -> None:
    assert telegram_id("8940919058@telegram.org") == 8940919058
    assert telegram_id("-1004001234567@telegram.org") == -1004001234567


def test_other_identifiers_are_left_to_the_server() -> None:
    assert telegram_id("123e4567-e89b-12d3-a456-426614174000") is None
    assert telegram_id("someone@example.com") is None
    assert telegram_id("@username@telegram.org") is None


@pytest.fixture
def context(tmp_path: Path, client: RecordingClient) -> Iterator[HandlerContext]:
    context = contexts.template(tmp_path, FederationService(ENDPOINT), cast(Client, client))
    context.managers.operators.save_operator(42, OperatorIdentity("operator-uuid", "token"))
    yield context
    context.database.close()


def answer(context: HandlerContext, client: RecordingClient) -> str:
    (sent,) = client.named("send_message")
    text: str = sent.kwargs["text"]
    return text


def text(context: HandlerContext, key: str, *args: object) -> str:
    return context.languages.get(context.languages.default_language, "blacklist", key, *args)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("command", "key", "args"),
    [
        ("/blacklist", "usage", ()),
        ("/blacklist a b c d e", "usage", ()),
        ("/blacklist 1 nonsense 30d", "invalid_type", ("nonsense",)),
        ("/blacklist spam forever", "invalid_expires", ("forever",)),
        ("/blacklist 1 not-a-uuid spam 30d", "invalid_report", ("not-a-uuid",)),
    ],
)
async def test_invalid_arguments_are_explained(
    context: HandlerContext,
    client: RecordingClient,
    command: str,
    key: str,
    args: tuple[object, ...],
) -> None:
    await BlacklistHandler().handle(context.with_update(updates.private_message(1, command)))

    assert answer(context, client) == text(context, key, *args)


@pytest.mark.anyio
async def test_a_named_user_is_blacklisted_by_their_telegram_address(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    context.managers.users.save_user(UserIdentity(99, "spammer", "Spammer", None))
    blacklist = respx_mock.post(f"{ENDPOINT}/blacklist").respond(json="blacklist-uuid")

    command = f"/blacklist @Spammer {REPORT} spam 12h"
    await BlacklistHandler().handle(context.with_update(updates.private_message(1, command)))

    assert b'"entity_identifier":"99@telegram.org"' in blacklist.calls.last.request.content
    assert "blacklist-uuid" in answer(context, client)


@pytest.mark.anyio
async def test_only_an_operator_may_blacklist(
    context: HandlerContext, client: RecordingClient
) -> None:
    context.managers.operators.delete_operator(42)

    await BlacklistHandler().handle(
        context.with_update(updates.private_message(1, f"/blacklist 1 {REPORT} spam 1d"))
    )

    assert answer(context, client) == text(context, "operator_only")
