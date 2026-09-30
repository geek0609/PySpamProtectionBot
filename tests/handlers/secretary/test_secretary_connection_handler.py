import pytest
from pyrogram.types import BusinessBotRights, BusinessConnection

from spb.enums import SecretaryContactStatus
from spb.handlers.secretary import secretary_connection_handler, secretary_settings_handler
from spb.handlers.secretary.secretary_connection_handler import SecretaryConnectionHandler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.secretary_contact import SecretaryContact
from spb.objects.update import Update
from tests.support import updates
from tests.support.recording_client import RecordingClient

OWNER_A = 101
OWNER_B = 202


def contact(context: HandlerContext, connection_id: str, contact_id: int) -> None:
    context.managers.secretary_contacts.register_secretary_contact(
        SecretaryContact(connection_id, contact_id, SecretaryContactStatus.ALLOWED, 1)
    )


def is_tracked(context: HandlerContext, connection_id: str, contact_id: int) -> bool:
    contacts = context.managers.secretary_contacts
    return contacts.get_secretary_contact(connection_id, contact_id) is not None


# Disabling


def test_disabling_one_owner_never_touches_another_owners_contacts(
    template: HandlerContext,
) -> None:
    configurations = template.managers.secretary_configurations
    configurations.set_business_connection_id(OWNER_A, "conn-a")
    configurations.set_business_connection_id(OWNER_B, "conn-b")
    contact(template, "conn-a", 1)
    contact(template, "conn-b", 2)

    secretary_connection_handler.apply_disable(template, OWNER_A, "conn-a")
    assert not is_tracked(template, "conn-a", 1)
    assert is_tracked(template, "conn-b", 2)

    # A repeated disable after owner A's configuration is gone only touches the connection named
    # in the update, never another owner's contacts.
    secretary_connection_handler.apply_disable(template, OWNER_A, "conn-a")
    assert is_tracked(template, "conn-b", 2)


def test_disabling_releases_the_connections_contact_claims(template: HandlerContext) -> None:
    template.managers.secretary_configurations.set_business_connection_id(OWNER_A, "conn-a")
    template.cache.put("secretary-contact-claim:conn-a:1", False)
    template.cache.put("secretary-contact-claim:conn-b:2", False)

    secretary_connection_handler.apply_disable(template, OWNER_A, "conn-a")

    assert template.cache.get_if_present("secretary-contact-claim:conn-a:1") is None
    assert template.cache.get_if_present("secretary-contact-claim:conn-b:2") is not None
    configurations = template.managers.secretary_configurations
    assert not configurations.secretary_configuration_exists(OWNER_A)


# Connection updates


@pytest.mark.anyio
async def test_connecting_binds_the_connection_and_lists_missing_permissions(
    context: HandlerContext, client: RecordingClient
) -> None:
    await SecretaryConnectionHandler().handle(
        context.with_update(updates.business_connection(1, True))
    )

    configuration = context.managers.secretary_configurations.get_secretary_configuration(42)
    assert configuration is not None
    assert configuration.business_connection_id == "conn-1"
    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    permission = context.languages.get(lang, "secretary", "permission_can_delete_all_messages")
    assert permission in sent.kwargs["text"]
    (row,) = sent.kwargs["reply_markup"].inline_keyboard
    assert row[0].callback_data == secretary_settings_handler.OPEN_CALLBACK


@pytest.mark.anyio
async def test_connecting_with_every_right_lists_nothing_missing(
    context: HandlerContext, client: RecordingClient
) -> None:
    connection = BusinessConnection(
        id="conn-1",
        user=updates.TESTER,
        dc_id=2,
        date=updates.DATE,
        is_enabled=True,
        rights=BusinessBotRights(can_delete_all_messages=True),
    )

    await SecretaryConnectionHandler().handle(
        context.with_update(Update(1, business_connection=connection))
    )

    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    title = context.languages.get(lang, "secretary", "missing_permissions_title")
    assert title not in sent.kwargs["text"]


@pytest.mark.anyio
async def test_disconnecting_switches_secretary_mode_off(
    context: HandlerContext, client: RecordingClient
) -> None:
    context.managers.secretary_configurations.set_business_connection_id(42, "conn-1")

    await SecretaryConnectionHandler().handle(
        context.with_update(updates.business_connection(1, False))
    )

    assert not context.managers.secretary_configurations.secretary_configuration_exists(42)
    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    assert sent.kwargs["text"].startswith(
        context.languages.get(lang, "secretary", "disabled_title")
    )
