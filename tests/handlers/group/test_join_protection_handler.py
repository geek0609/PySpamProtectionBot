from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
import respx
from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.types import Chat, ChatJoinRequest, Message, User

from spb.classes.federation_service import FederationService
from spb.handlers.group.join_protection_handler import JoinProtectionHandler
from spb.objects.admin_info import AdminInfo
from spb.objects.context.handler_context import HandlerContext
from spb.objects.update import Update
from tests.support import contexts, updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"
GROUP = Chat(id=-1001234567890, type=ChatType.SUPERGROUP, title="Test Chat")
JOINER = User(id=99, is_bot=False, first_name="Joiner")
MODERATOR = 7


@pytest.fixture
def context(tmp_path: Path, client: RecordingClient) -> Iterator[HandlerContext]:
    context = contexts.template(tmp_path, FederationService(ENDPOINT), cast(Client, client))
    context.managers.chat_configurations.enable_chat_configuration(GROUP.id)
    context.chat_admins.put(
        GROUP.id,
        [
            AdminInfo(contexts.BOT.id, False, True, True, False),
            AdminInfo(MODERATOR, False, True, True, False),
        ],
    )
    yield context
    context.database.close()


def suggest(respx_mock: respx.MockRouter, suggested_action: str | None) -> None:
    entity = {
        "uuid": "entity-uuid",
        "hash": "hash",
        "host": "telegram.org",
        "id": "99",
        "whitelisted": False,
        "reputation": -10,
        "created": 1_700_000_000,
        "updated": 1_700_000_000,
    }
    respx_mock.get(f"{ENDPOINT}/entities/99@telegram.org/query").respond(
        json={
            "entity_record": entity,
            "related_entities": [],
            "active_blacklists": [],
            "suggested_action": suggested_action,
        }
    )


def joined() -> Update:
    message = Message(id=5, date=updates.DATE, chat=GROUP, new_chat_members=[JOINER])
    return Update(1, message=message)


def join_request() -> Update:
    request = ChatJoinRequest(chat=GROUP, from_user=JOINER, date=updates.DATE)
    return Update(1, chat_join_request=request)


async def test_a_flagged_member_is_banned_once_and_moderators_are_told(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    suggest(respx_mock, "PERMANENTLY_BLOCK_ENTITY")
    handler = JoinProtectionHandler()

    await handler.handle(context.with_update(joined()))
    await handler.handle(context.with_update(joined()))

    (ban,) = client.named("ban_chat_member")
    assert ban.args[:2] == (GROUP.id, JOINER.id)
    (notification,) = client.named("send_message")
    assert notification.args[0] == MODERATOR


async def test_a_member_without_a_blocking_suggestion_is_left_alone(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    suggest(respx_mock, None)

    await JoinProtectionHandler().handle(context.with_update(joined()))

    assert client.calls == []


async def test_a_flagged_join_request_is_declined(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    suggest(respx_mock, "TEMPORARILY_BLOCK_ENTITY")

    await JoinProtectionHandler().handle(context.with_update(join_request()))

    assert [call.method for call in client.calls] == ["decline_chat_join_request", "send_message"]


async def test_an_unflagged_join_request_is_approved_quietly(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    suggest(respx_mock, "CAUTION")

    await JoinProtectionHandler().handle(context.with_update(join_request()))

    assert [call.method for call in client.calls] == ["approve_chat_join_request"]
