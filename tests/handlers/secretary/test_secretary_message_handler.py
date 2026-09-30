import pytest
from pyrogram.enums import ChatType
from pyrogram.types import Chat, Message
from pyrogram.types.messages_and_media.message import Str

from spb.enums import SecretaryContactStatus
from spb.handlers.secretary.secretary_message_handler import SecretaryMessageHandler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.update import Update
from tests.support import updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

OWNER = updates.TESTER.id
CONTACT = 99


@pytest.fixture
def secretary(context: HandlerContext) -> HandlerContext:
    """A context whose user has secretary mode enabled on business connection conn-1."""
    configurations = context.managers.secretary_configurations
    configurations.create_secretary_configuration(OWNER)
    configurations.set_business_connection_id(OWNER, "conn-1")
    return context


def status(context: HandlerContext) -> SecretaryContactStatus | None:
    contact = context.managers.secretary_contacts.get_secretary_contact("conn-1", CONTACT)
    return contact.status if contact is not None else None


async def test_a_denied_contact_is_deleted_without_a_notification(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    contacts = secretary.managers.secretary_contacts
    contacts.set_secretary_contact_status("conn-1", CONTACT, SecretaryContactStatus.DENIED)

    await SecretaryMessageHandler().handle(secretary.with_update(updates.business_message(1, "hi")))

    (deleted,) = client.named("delete_business_messages")
    assert deleted.args == ("conn-1", [1])
    assert client.named("send_message") == []


async def test_the_owner_decides_about_a_contact_from_their_chat(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    command = Message(
        id=2,
        date=updates.DATE,
        chat=Chat(id=CONTACT, type=ChatType.PRIVATE),
        from_user=updates.TESTER,
        text=Str("/deny"),
        business_connection_id="conn-1",
    )

    await SecretaryMessageHandler().handle(
        secretary.with_update(Update(1, business_message=command))
    )

    assert status(secretary) is SecretaryContactStatus.DENIED


async def test_a_contact_nobody_could_check_stays_unknown(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    await SecretaryMessageHandler().handle(secretary.with_update(updates.business_message(1, "hi")))

    assert status(secretary) is SecretaryContactStatus.UNKNOWN
    assert client.calls == []


async def test_a_connection_without_secretary_mode_is_ignored(
    context: HandlerContext, client: RecordingClient
) -> None:
    await SecretaryMessageHandler().handle(context.with_update(updates.business_message(1, "hi")))

    assert status(context) is None
    assert client.calls == []
