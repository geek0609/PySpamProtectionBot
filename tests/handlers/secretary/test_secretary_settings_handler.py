from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
import respx
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup

from spb.classes.federation_service import FederationService
from spb.enums import ScanningBehavior, SecretaryContactStatus
from spb.handlers.secretary import secretary_settings_handler
from spb.handlers.secretary.secretary_settings_handler import SecretarySettingsHandler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.secretary_report_target import SecretaryReportTarget
from spb.objects.update import Update
from tests.support import contexts, payloads, updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"
OWNER = updates.TESTER.id


def callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.fixture
def secretary(context: HandlerContext) -> HandlerContext:
    """A context whose user has secretary mode enabled on business connection conn-1."""
    configurations = context.managers.secretary_configurations
    configurations.create_secretary_configuration(OWNER)
    configurations.set_business_connection_id(OWNER, "conn-1")
    return context


# Routing


@pytest.mark.parametrize(
    ("update", "accepted"),
    [
        (updates.private_message(1, "/start"), False),
        (updates.private_message(1, "/start bizChat99"), True),
        (updates.private_message(1, "/settings"), True),
        (updates.group_message(1, "/settings"), False),
        (updates.callback_query(1, "secset:open"), True),
    ],
)
def test_it_claims_only_private_settings_and_business_deep_links(
    context: HandlerContext, update: Update, accepted: bool
) -> None:
    assert SecretarySettingsHandler().accepts(context.with_update(update)) is accepted


# Commands


async def test_a_business_deep_link_enables_secretary_mode(
    context: HandlerContext, client: RecordingClient
) -> None:
    update = updates.private_message(1, "/start bizChat99")

    await SecretarySettingsHandler().handle(context.with_update(update))

    assert context.managers.secretary_configurations.secretary_configuration_exists(OWNER)
    (sent,) = client.named("send_message")
    assert callbacks(sent.kwargs["reply_markup"]) == [secretary_settings_handler.OPEN_CALLBACK]


async def test_a_deep_link_offers_a_decision_about_the_contact(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    update = updates.private_message(1, "/start bizChat99")

    await SecretarySettingsHandler().handle(secretary.with_update(update))

    (sent,) = client.named("send_message")
    assert callbacks(sent.kwargs["reply_markup"]) == [
        "seccontactsettings:conn-1:99:allow",
        "seccontactsettings:conn-1:99:deny",
        secretary_settings_handler.OPEN_CALLBACK,
    ]


async def test_settings_opens_the_menu(secretary: HandlerContext, client: RecordingClient) -> None:
    await SecretarySettingsHandler().handle(
        secretary.with_update(updates.private_message(1, "/settings"))
    )

    (sent,) = client.named("send_message")
    assert sent.kwargs["chat_id"] == OWNER
    behavior, scanning = callbacks(sent.kwargs["reply_markup"])
    assert behavior.endswith(":set_behavior:PASSIVE")
    assert scanning.endswith(":set_scanning:on")


async def test_settings_without_secretary_mode_says_so(
    context: HandlerContext, client: RecordingClient
) -> None:
    await SecretarySettingsHandler().handle(
        context.with_update(updates.private_message(1, "/settings"))
    )

    (sent,) = client.named("send_message")
    lang = context.languages.default_language
    assert sent.kwargs["text"] == context.languages.get(lang, "settings", "secretary_not_enabled")
    assert not context.managers.secretary_configurations.secretary_configuration_exists(OWNER)


# Menu


async def test_changing_the_behavior_saves_it_and_refreshes_the_menu(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    session = secretary.sessions.configuration.create(OWNER, OWNER)
    data = f"secset:{session.hash}:set_behavior:PASSIVE"

    await SecretarySettingsHandler().handle(secretary.with_update(updates.callback_query(1, data)))

    configuration = secretary.managers.secretary_configurations.resolve(OWNER)
    assert configuration.behavior is ScanningBehavior.PASSIVE
    (edit,) = client.named("edit_message_text")
    assert callbacks(edit.kwargs["reply_markup"]) == [
        f"secset:{session.hash}:set_behavior:STRICT",
        f"secset:{session.hash}:set_scanning:on",
    ]


async def test_turning_scanning_on_saves_it_and_refreshes_the_menu(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    session = secretary.sessions.configuration.create(OWNER, OWNER)
    data = f"secset:{session.hash}:set_scanning:on"

    await SecretarySettingsHandler().handle(secretary.with_update(updates.callback_query(1, data)))

    assert secretary.managers.secretary_configurations.resolve(OWNER).scanning_enabled
    (edit,) = client.named("edit_message_text")
    assert callbacks(edit.kwargs["reply_markup"])[-1] == f"secset:{session.hash}:set_scanning:off"


async def test_an_expired_session_says_so(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    update = updates.callback_query(1, "secset:gone:set_behavior:PASSIVE")

    await SecretarySettingsHandler().handle(secretary.with_update(update))

    configuration = secretary.managers.secretary_configurations.resolve(OWNER)
    assert configuration.behavior is ScanningBehavior.STRICT
    (edit,) = client.named("edit_message_text")
    lang = secretary.languages.default_language
    assert edit.kwargs["text"] == secretary.languages.get(
        lang, "secretary_settings", "session_expired"
    )


async def test_opening_from_start_leads_back_to_it(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    update = updates.callback_query(1, secretary_settings_handler.OPEN_WITH_BACK_CALLBACK)

    await SecretarySettingsHandler().handle(secretary.with_update(update))

    (edit,) = client.named("edit_message_text")
    assert callbacks(edit.kwargs["reply_markup"])[-1].endswith(":start")


# Contact decisions


async def test_denying_a_contact_leaves_only_allow(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    update = updates.callback_query(1, "seccontact:conn-1:99:deny")

    await SecretarySettingsHandler().handle(secretary.with_update(update))

    contact = secretary.managers.secretary_contacts.get_secretary_contact("conn-1", 99)
    assert contact is not None
    assert contact.status is SecretaryContactStatus.DENIED
    (edit,) = client.named("edit_message_reply_markup")
    assert callbacks(edit.args[2]) == ["seccontact:conn-1:99:allow"]


async def test_a_decision_from_the_contact_page_redraws_it(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    update = updates.callback_query(1, "seccontactsettings:conn-1:99:allow")

    await SecretarySettingsHandler().handle(secretary.with_update(update))

    (edit,) = client.named("edit_message_text")
    assert callbacks(edit.kwargs["reply_markup"]) == [
        "seccontactsettings:conn-1:99:deny",
        secretary_settings_handler.OPEN_CALLBACK,
    ]


async def test_a_decision_about_another_connection_is_rejected(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    update = updates.callback_query(1, "seccontact:conn-2:99:deny")

    await SecretarySettingsHandler().handle(secretary.with_update(update))

    assert secretary.managers.secretary_contacts.get_secretary_contact("conn-2", 99) is None
    assert client.named("edit_message_reply_markup") == []


# Report button


@pytest.fixture
async def authenticated(
    tmp_path: Path, client: RecordingClient, respx_mock: respx.MockRouter
) -> AsyncIterator[HandlerContext]:
    """A context with secretary mode enabled and the bot authenticated with client permissions."""
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(
        json={
            "uuid": "0192aaaa-0000-7000-8000-000000000001",
            "name": "bot",
            "disabled": False,
            "permissions": {"client": True, "management": False, "operator": False},
            "configuration": {
                "auto_assign_reports": False,
                "auto_assign_automated_reports": False,
                "is_bot": False,
            },
            "created": 1_700_000_000,
            "updated": 1_700_000_000,
        }
    )
    respx_mock.get(f"{ENDPOINT}/info").respond(json=payloads.server_information())
    federation = FederationService(ENDPOINT, "token")
    await federation.authenticate()
    context = contexts.template(tmp_path, federation, cast(Client, client))
    context.managers.secretary_configurations.create_secretary_configuration(OWNER)
    yield context
    context.database.close()


async def test_a_report_button_opens_a_report_dialog_for_the_message(
    authenticated: HandlerContext, client: RecordingClient
) -> None:
    target = SecretaryReportTarget(OWNER, 99, 5, "buy now", [], {"text": "buy now"})
    token = authenticated.sessions.secretary_reports.store(target)
    update = updates.callback_query(
        7, f"{secretary_settings_handler.REPORT_CALLBACK_PREFIX}:{token}"
    )

    await SecretarySettingsHandler().handle(authenticated.with_update(update))

    (prompt,) = client.named("send_message")
    assert prompt.args[0] == OWNER
    assert prompt.kwargs["reply_parameters"].message_id == 7
    # The recording client numbers each sent message by its position among the calls.
    prompt_id = client.calls.index(prompt) + 1
    session = authenticated.sessions.report.find_by_prompt_message_id(OWNER, prompt_id)
    assert session is not None
    assert (session.target_author_id, session.target_message_id) == (99, 5)


async def test_an_expired_report_button_says_so(
    secretary: HandlerContext, client: RecordingClient
) -> None:
    update = updates.callback_query(1, f"{secretary_settings_handler.REPORT_CALLBACK_PREFIX}:gone")

    await SecretarySettingsHandler().handle(secretary.with_update(update))

    (answer,) = client.named("answer_callback_query")
    lang = secretary.languages.default_language
    expired = secretary.languages.get(lang, "secretary", "report_expired")
    assert answer.kwargs["text"] == expired
    assert client.named("send_message") == []
