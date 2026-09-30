"""Tests when scanning acts on a message and when it tells a chat's moderators about a member, so a
#SCAN_MATCH is only ever sent for something that was flagged."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
import respx
from pyfederation.enums import SuggestedAction
from pyrogram import Client
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import Chat, ChatMember, Message, User
from pyrogram.types.messages_and_media.message import Str

from spb.classes.federation_service import FederationService
from spb.enums import ScanningBehavior
from spb.handlers.group import scanning_handler
from spb.handlers.group.scanning_handler import ScanningHandler
from spb.objects.admin_info import AdminInfo
from spb.objects.context.handler_context import HandlerContext
from spb.objects.update import Update
from tests.support import contexts, updates
from tests.support.recording_client import RecordingClient

ENDPOINT = "http://127.0.0.1:1"
GROUP = Chat(id=-1003540281179, type=ChatType.SUPERGROUP, title="The Midnight Channel")
MEMBER = User(id=99, is_bot=False, first_name="Member")
MODERATOR = 7


def message(**fields: Any) -> Message:
    return Message(id=7, date=updates.DATE, chat=GROUP, from_user=MEMBER, **fields)


# Deciding what is worth a notification


def test_a_members_first_message_that_nothing_flagged_sends_no_notification(
    template: HandlerContext,
) -> None:
    hello = message(text=Str("hi"))

    assert not scanning_handler.passive_observation_due(template, hello, None, None)


def test_a_flag_is_reported_once_and_coming_back_clean_is_silent(
    template: HandlerContext,
) -> None:
    hello = message(text=Str("hi"))
    due = scanning_handler.passive_observation_due
    caution = SuggestedAction.CAUTION

    assert due(template, hello, caution, None)
    assert not due(template, hello, caution, None)
    assert not due(template, hello, None, None)
    assert due(template, hello, caution, None), "flagged again after a clean message is new again"


def test_a_caution_alone_is_recognised() -> None:
    caution_only = scanning_handler.is_caution_only
    caution = SuggestedAction.CAUTION

    assert caution_only(caution, None)
    assert caution_only(None, caution)
    assert caution_only(caution, caution)
    assert not caution_only(None, None), "nothing flagged is not a caution"
    assert not caution_only(SuggestedAction.BLOCK_CONTENT, caution)
    assert not caution_only(caution, SuggestedAction.PERMANENTLY_BLOCK_ENTITY)


def test_members_joining_or_leaving_are_left_to_join_protection() -> None:
    service = scanning_handler.is_membership_service_message

    assert service(message(new_chat_members=[MEMBER]))
    assert service(message(left_chat_member=MEMBER))
    assert not service(message(text=Str("hello")))


def test_posts_the_linked_channel_forwards_into_the_group_are_recognised() -> None:
    post = message(text=Str("New post"), automatic_forward=True)

    assert scanning_handler.is_linked_channel_post(post)
    assert not scanning_handler.is_linked_channel_post(message(text=Str("hello")))


# Acting on a scan


@pytest.fixture
async def context(
    tmp_path: Path, client: RecordingClient, respx_mock: respx.MockRouter
) -> AsyncIterator[HandlerContext]:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(
        json={
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
    )
    respx_mock.post(f"{ENDPOINT}/entities").respond(json="entity-uuid")
    respx_mock.get(f"{ENDPOINT}/entities/99@telegram.org/query").respond(404, json={})
    federation = FederationService(ENDPOINT, "token")
    await federation.authenticate()
    context = contexts.template(tmp_path, federation, cast(Client, client))
    context.managers.chat_configurations.enable_chat_configuration(GROUP.id)
    context.chat_admins.put(
        GROUP.id,
        [
            AdminInfo(contexts.BOT.id, False, True, True, False),
            AdminInfo(MODERATOR, False, True, True, False),
        ],
    )
    member = ChatMember(status=ChatMemberStatus.MEMBER, user=MEMBER)
    client.respond("get_chat_member", member)
    yield context
    context.database.close()


def scanned(respx_mock: respx.MockRouter, suggested_action: str | None) -> respx.Route:
    return respx_mock.post(f"{ENDPOINT}/scan").respond(
        json={
            "resolved_entities": [],
            "classification": None,
            "suggested_action": suggested_action,
            "scan_results": {},
            "risk_score": 90.0,
        }
    )


@pytest.mark.anyio
async def test_blocked_content_is_forwarded_deleted_and_reported(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    scanned(respx_mock, "BLOCK_CONTENT")
    spam = message(text=Str("buy now at spam.example"))

    await ScanningHandler().handle(context.with_update(Update(1, message=spam)))

    assert [call.method for call in client.calls if call.method != "get_chat_member"] == [
        "forward_messages",
        "delete_messages",
        "send_message",
    ]
    (notification,) = client.named("send_message")
    assert notification.args[0] == MODERATOR


@pytest.mark.anyio
async def test_a_passive_chat_is_only_told(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    scanned(respx_mock, "BLOCK_CONTENT")
    context.managers.chat_configurations.set_scanning_behavior(GROUP.id, ScanningBehavior.PASSIVE)

    await ScanningHandler().handle(context.with_update(Update(1, message=message(text=Str("x")))))

    assert client.named("delete_messages") == []
    assert len(client.named("send_message")) == 1


@pytest.mark.anyio
async def test_a_clean_message_is_left_alone(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    scanned(respx_mock, None)

    await ScanningHandler().handle(context.with_update(Update(1, message=message(text=Str("hi")))))

    assert [call.method for call in client.calls] == ["get_chat_member"]


@pytest.mark.anyio
async def test_a_linked_channel_post_is_never_scanned(
    context: HandlerContext, client: RecordingClient, respx_mock: respx.MockRouter
) -> None:
    scan = scanned(respx_mock, "BLOCK_CONTENT")
    post = message(text=Str("buy now at spam.example"), automatic_forward=True)

    await ScanningHandler().handle(context.with_update(Update(1, message=post)))

    assert not scan.called
    assert client.calls == []
