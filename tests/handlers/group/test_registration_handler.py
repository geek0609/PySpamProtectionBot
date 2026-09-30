"""Tests that the bookkeeping RegistrationHandler keeps current when a chat changes under it."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import (
    Chat,
    ChatAdministratorRights,
    ChatMember,
    ChatMemberUpdated,
    Message,
    User,
)
from pyrogram.types.messages_and_media.message import Str

from spb.classes.federation_service import FederationService
from spb.handlers.group import registration_handler
from spb.handlers.group.registration_handler import RegistrationHandler
from spb.objects.admin_info import AdminInfo
from spb.objects.context.handler_context import HandlerContext
from spb.objects.update import Update
from tests.support import contexts, updates

pytestmark = pytest.mark.anyio

BASIC_GROUP = -4001234567
SUPERGROUP = -1004001234567
STALE_ADMINS = [AdminInfo(42, True, True, True, True)]
OWNER = User(id=42, is_bot=False, first_name="Owner")


async def handle(context: HandlerContext, update: Update) -> None:
    await RegistrationHandler().handle(context.with_update(update))


def member(user_id: int, status: ChatMemberStatus) -> ChatMember:
    user = User(id=user_id, is_bot=False, first_name="Member")
    if status is ChatMemberStatus.ADMINISTRATOR:
        rights = ChatAdministratorRights(can_manage_chat=True, can_change_info=True)
        return ChatMember(status=status, user=user, privileges=rights)
    return ChatMember(status=status, user=user)


def membership(
    key: str, user_id: int, old_status: ChatMemberStatus, new_status: ChatMemberStatus
) -> Update:
    change = ChatMemberUpdated(
        chat=Chat(id=BASIC_GROUP, type=ChatType.GROUP, title="Test Chat"),
        from_user=OWNER,
        date=updates.DATE,
        old_chat_member=member(user_id, old_status),
        new_chat_member=member(user_id, new_status),
    )
    return Update(1, **{key: change})


def message_in(
    chat_id: int, chat_type: ChatType, from_user: User = OWNER, **fields: object
) -> Update:
    chat = Chat(id=chat_id, type=chat_type, title="Test Chat")
    message = Message(
        id=7, date=updates.DATE, chat=chat, from_user=from_user, text=Str("hello"), **fields
    )
    return Update(1, message=message)


# Keeping the administrator list current


async def test_promoting_the_bot_drops_the_cached_administrator_list(
    template: HandlerContext,
) -> None:
    template.chat_admins.put(BASIC_GROUP, STALE_ADMINS)

    await handle(
        template,
        membership(
            "my_chat_member",
            contexts.BOT.id,
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
        ),
    )

    assert template.chat_admins.get_if_present(BASIC_GROUP) is None


async def test_demoting_another_administrator_drops_the_cached_list(
    template: HandlerContext,
) -> None:
    template.chat_admins.put(BASIC_GROUP, STALE_ADMINS)

    await handle(
        template,
        membership("chat_member", 77, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER),
    )

    assert template.chat_admins.get_if_present(BASIC_GROUP) is None


async def test_an_ordinary_member_joining_leaves_the_cached_list_alone(
    template: HandlerContext,
) -> None:
    template.chat_admins.put(BASIC_GROUP, STALE_ADMINS)

    await handle(
        template, membership("chat_member", 77, ChatMemberStatus.LEFT, ChatMemberStatus.MEMBER)
    )

    assert template.chat_admins.get_if_present(BASIC_GROUP) == STALE_ADMINS


# Detecting a user's language


def speaking(language_code: str) -> Update:
    user = User(id=55, is_bot=False, first_name="Member", language_code=language_code)
    return message_in(SUPERGROUP, ChatType.SUPERGROUP, user)


async def test_an_untranslated_client_language_leaves_the_default_in_place(
    template: HandlerContext,
) -> None:
    await handle(template, speaking("de"))

    preferences = template.managers.language_preferences
    assert preferences.get_user_language(55) == template.languages.default_language


async def test_a_translated_client_language_is_adopted(template: HandlerContext) -> None:
    await handle(template, speaking("es"))

    preferences = template.managers.language_preferences
    assert preferences.get_user_language(55) == template.languages.resolve("es")


# Following a basic group upgraded to a supergroup


async def test_the_configuration_and_language_follow_the_group_to_its_new_id(
    template: HandlerContext,
) -> None:
    configurations = template.managers.chat_configurations
    spanish = template.languages.resolve("es")
    assert spanish is not None
    configurations.enable_chat_configuration(BASIC_GROUP)
    configurations.set_scanning_enabled(BASIC_GROUP, False)
    template.managers.language_preferences.set_chat_language(BASIC_GROUP, spanish)

    await handle(template, message_in(BASIC_GROUP, ChatType.GROUP, migrate_to_chat_id=SUPERGROUP))

    moved = configurations.get_chat_configuration(SUPERGROUP)
    assert moved is not None
    assert moved.enabled
    assert not moved.scanning_enabled, "customised settings move too"
    assert template.managers.language_preferences.get_chat_language(SUPERGROUP) == spanish
    assert not configurations.chat_configuration_exists(BASIC_GROUP)


async def test_the_announcement_in_the_new_supergroup_moves_it_just_the_same(
    template: HandlerContext,
) -> None:
    template.managers.chat_configurations.enable_chat_configuration(BASIC_GROUP)

    await handle(
        template,
        message_in(SUPERGROUP, ChatType.SUPERGROUP, migrate_from_chat_id=BASIC_GROUP),
    )

    assert template.managers.chat_configurations.resolve(SUPERGROUP).enabled


async def test_both_announcements_move_it_once_and_overwrite_nothing(
    template: HandlerContext,
) -> None:
    configurations = template.managers.chat_configurations
    configurations.enable_chat_configuration(BASIC_GROUP)

    await handle(template, message_in(BASIC_GROUP, ChatType.GROUP, migrate_to_chat_id=SUPERGROUP))
    configurations.set_scanning_enabled(SUPERGROUP, False)
    await handle(
        template,
        message_in(SUPERGROUP, ChatType.SUPERGROUP, migrate_from_chat_id=BASIC_GROUP),
    )

    configuration = configurations.get_chat_configuration(SUPERGROUP)
    assert configuration is not None
    assert not configuration.scanning_enabled, "a later change on the new id survives"


# Deciding whether entities are pushed to Federation


@pytest.fixture
def configured(tmp_path: Path) -> Iterator[HandlerContext]:
    context = contexts.template(tmp_path, FederationService("http://127.0.0.1:1/"))
    yield context
    context.database.close()


def decide(context: HandlerContext, chat_id: int, chat_type: ChatType) -> bool:
    message = message_in(chat_id, chat_type).message
    assert message is not None
    return registration_handler.should_push_entities(context, message)


def test_a_private_chat_with_the_bot_always_pushes(configured: HandlerContext) -> None:
    assert decide(configured, 42, ChatType.PRIVATE)


def test_nothing_is_pushed_when_federation_is_not_configured(template: HandlerContext) -> None:
    assert not decide(template, 42, ChatType.PRIVATE)


def test_a_group_pushes_only_when_enabled_without_privacy_mode(
    configured: HandlerContext,
) -> None:
    configurations = configured.managers.chat_configurations
    assert not decide(configured, SUPERGROUP, ChatType.SUPERGROUP), "not enabled there"

    configurations.enable_chat_configuration(SUPERGROUP)
    configurations.set_privacy_mode(SUPERGROUP, True)
    assert not decide(configured, SUPERGROUP, ChatType.SUPERGROUP), "privacy mode suppresses it"

    configurations.set_privacy_mode(SUPERGROUP, False)
    assert decide(configured, SUPERGROUP, ChatType.SUPERGROUP)
