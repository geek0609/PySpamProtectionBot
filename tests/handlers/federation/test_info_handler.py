"""Tests for how /info chooses the entity it looks up."""

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
import respx
from pyrogram import Client
from pyrogram.enums import ChatType, MessageEntityType
from pyrogram.types import Chat, Message, MessageEntity, User
from pyrogram.types.messages_and_media.message import Str

from spb.classes.federation_service import FederationService
from spb.handlers.federation.info_handler import InfoHandler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.user_identity import UserIdentity
from spb.objects.update import Update
from tests.support import contexts, updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"
GROUP = Chat(id=-1001234567890, type=ChatType.SUPERGROUP, title="Group")


@pytest.fixture
def context(tmp_path: Path, client: RecordingClient) -> Iterator[HandlerContext]:
    federation = FederationService(ENDPOINT)
    context = contexts.template(tmp_path, federation, cast(Client, client))
    yield context
    context.database.close()


def command(text: str, **fields: object) -> Update:
    message = Message(
        id=1, date=updates.DATE, chat=GROUP, from_user=updates.TESTER, text=Str(text), **fields
    )
    return Update(1, message=message)


async def queried(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter, update: Update
) -> str:
    """Runs /info and returns the identifier it looked up, which the not-found answer names."""
    respx_mock.get(url__startswith=f"{ENDPOINT}/entities/").respond(404, json={})

    await InfoHandler().handle(context.with_update(update))

    (answer,) = client.named("send_message")
    text: str = answer.kwargs["text"]
    return text.split("<code>", 1)[1].split("</code>", 1)[0]


async def test_without_an_argument_or_a_reply_the_caller_is_looked_up(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    assert await queried(context, client, respx_mock, command("/info")) == "42@telegram.org"


async def test_a_reply_looks_up_the_replied_messages_author(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    target = Message(
        id=50, date=updates.DATE, chat=GROUP, from_user=User(id=99, first_name="Spammer")
    )
    update = command("/info", reply_to_message_id=50, reply_to_message=target)

    assert await queried(context, client, respx_mock, update) == "99@telegram.org"


@pytest.mark.parametrize(
    ("argument", "identifier"),
    [
        ("123", "123@telegram.org"),
        ("123@telegram.org", "123@telegram.org"),
        ("0192aaaa-0000-7000-8000-000000000002", "0192aaaa-0000-7000-8000-000000000002"),
        ("@Tester", "42@telegram.org"),
    ],
)
async def test_an_argument_is_resolved_by_its_shape(
    context: HandlerContext,
    client: RecordingClient,
    respx_mock: respx.MockRouter,
    argument: str,
    identifier: str,
) -> None:
    context.managers.users.save_user(UserIdentity(42, "tester", "Tester", None))

    assert await queried(context, client, respx_mock, command(f"/info {argument}")) == identifier


async def test_a_mention_is_found_after_characters_outside_the_bmp(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    # Entity offsets count UTF-16 code units, so the emoji before the mention takes two of them.
    text = f"/info {chr(0x1F600)} @tester"
    mention = MessageEntity(type=MessageEntityType.MENTION, offset=9, length=7)
    context.managers.users.save_user(UserIdentity(42, "tester", "Tester", None))

    update = command(text, entities=[mention])

    assert await queried(context, client, respx_mock, update) == "42@telegram.org"
