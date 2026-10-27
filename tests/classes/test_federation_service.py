import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
import respx
from pyfederation.enums import EntityRelationshipType, IncidentType
from pyfederation.records import ContentInput

from spb.classes.federation_service import FederationService
from spb.exceptions import FederationException

ENDPOINT = "http://127.0.0.1:1"

OPERATOR: dict[str, Any] = {
    "uuid": "0192aaaa-0000-7000-8000-000000000001",
    "name": "bot",
    "disabled": False,
    "permissions": {"client": True, "management": False, "operator": False},
    "configuration": {
        "auto_assign_reports": False,
        "auto_assign_automated_reports": False,
        "is_bot": True,
    },
    "created": 1_700_000_000,
    "updated": 1_700_000_000,
}

type FederationCall = Callable[[FederationService], Awaitable[Any]]

# Running without a Federation server


def test_reports_unavailable() -> None:
    unavailable = FederationService.unavailable()

    assert not unavailable.is_available
    assert unavailable.endpoint is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "call",
    [
        lambda service: service.server_information(),
        lambda service: service.operator_for("token"),
        lambda service: service.entity("1@telegram.org"),
        lambda service: service.query_entity("1@telegram.org"),
        lambda service: service.publish_entity("telegram.org", "1", None),
        lambda service: service.link_entities(None, "a", "b", EntityRelationshipType.ALTERNATIVE),
        lambda service: service.scan_content(ContentInput(text_content="hi"), None),
        lambda service: service.active_blacklists("1@telegram.org"),
        lambda service: service.blacklist_entity(None, "e", "r", IncidentType.SPAM, 60),
        lambda service: service.report("uuid"),
        lambda service: service.entity_reports("e", 5),
        lambda service: service.open_assigned_reports("token", 5),
        lambda service: service.submit_report(
            None, "1@telegram.org", ContentInput(text_content="buy now"), IncidentType.SPAM, None
        ),
        lambda service: service.close_report("token", "uuid", None),
        lambda service: service.evidence_as("token", "uuid"),
        lambda service: service.report_evidence("uuid", 5),
        lambda service: service.evidence_attachments(None, "uuid"),
        lambda service: service.download_attachment(None, "uuid", "/tmp"),
        lambda service: service.upload_attachment(None, "uuid", "/tmp/f", "f"),
    ],
)
async def test_every_operation_fails_cleanly(call: FederationCall) -> None:
    with pytest.raises(FederationException, match="not configured"):
        await call(FederationService.unavailable())


@pytest.mark.anyio
async def test_closing_is_harmless_and_it_is_a_singleton() -> None:
    unavailable = FederationService.unavailable()

    await unavailable.close()
    await unavailable.close()

    assert FederationService.unavailable() is unavailable


# Connected without client permissions


def test_anonymous_is_available_but_not_authenticated() -> None:
    anonymous = FederationService(ENDPOINT)

    assert anonymous.is_available
    assert not anonymous.is_authenticated


@pytest.mark.anyio
async def test_authenticating_anonymous_is_a_no_op() -> None:
    anonymous = FederationService(ENDPOINT)

    assert await anonymous.authenticate() is None
    assert not anonymous.is_authenticated


@pytest.mark.anyio
@pytest.mark.parametrize(
    "call",
    [
        lambda service: service.publish_entity("telegram.org", "1", None),
        lambda service: service.submit_report(
            None, "1@telegram.org", ContentInput(text_content="hi"), IncidentType.SPAM, None
        ),
        lambda service: service.upload_attachment(None, "uuid", "/tmp/f", "f"),
    ],
)
async def test_writes_as_the_bot_require_client_permissions(call: FederationCall) -> None:
    # No route is mocked, so reaching the network would fail differently.
    with respx.mock, pytest.raises(FederationException, match="client permissions"):
        await call(FederationService(ENDPOINT))


# Connected with an access token


@pytest.mark.anyio
async def test_a_token_with_client_permissions_authenticates(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(json=OPERATOR)
    service = FederationService(ENDPOINT, "token")

    operator = await service.authenticate()

    assert operator is not None
    assert operator.name == "bot"
    assert service.is_authenticated


@pytest.mark.anyio
async def test_a_token_without_client_permissions_does_not(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(
        json=OPERATOR | {"permissions": {"client": False, "management": False, "operator": False}}
    )
    service = FederationService(ENDPOINT, "token")

    await service.authenticate()

    assert not service.is_authenticated
    assert not service.is_authentication_pending


@pytest.mark.anyio
async def test_a_rejected_token_is_discarded(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(
        401, json={"success": False, "message": "Unauthorized: Invalid access token"}
    )
    reports = respx_mock.get(f"{ENDPOINT}/reports/uuid").respond(404, json={})
    service = FederationService(ENDPOINT, "token")

    with pytest.raises(FederationException) as rejected:
        await service.authenticate()
    await service.report("uuid")

    assert FederationService.is_credential_rejected(rejected.value)
    assert not service.is_authentication_pending
    assert "Authorization" not in reports.calls.last.request.headers


@pytest.mark.anyio
async def test_an_unreachable_server_keeps_the_token_and_leaves_authentication_pending(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{ENDPOINT}/operators/self").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    service = FederationService(ENDPOINT, "token")
    assert service.is_authentication_pending

    with pytest.raises(FederationException) as failed:
        await service.authenticate()

    assert not FederationService.is_credential_rejected(failed.value)
    assert not service.is_authenticated
    assert service.is_authentication_pending


@pytest.mark.anyio
async def test_a_missing_record_is_none(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{ENDPOINT}/reports/uuid").respond(
        404, json={"success": False, "message": "Not Found: Report not found"}
    )

    assert await FederationService(ENDPOINT).report("uuid") is None


@pytest.mark.anyio
async def test_a_server_error_mentioning_not_found_is_an_error(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{ENDPOINT}/reports/uuid").respond(
        500, json={"success": False, "message": "Database row not found"}
    )

    with pytest.raises(FederationException, match="not found"):
        await FederationService(ENDPOINT).report("uuid")


# Configuring the bot's own operator


NOT_A_BOT = OPERATOR | {"configuration": OPERATOR["configuration"] | {"is_bot": False}}


@pytest.mark.anyio
async def test_an_operator_that_is_not_a_bot_is_configured_as_one(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(json=NOT_A_BOT)
    configure = respx_mock.patch(f"{ENDPOINT}/operators/{OPERATOR['uuid']}/configuration").respond(
        json=OPERATOR["configuration"]
    )
    service = FederationService(ENDPOINT, "token")

    operator = await service.authenticate()

    assert operator is not None
    assert operator.is_bot
    assert service.is_authenticated
    assert json.loads(configure.calls.last.request.content) == {"is_bot": True}


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["root", "System"])
async def test_root_and_system_are_not_configured_as_bots(
    respx_mock: respx.MockRouter, name: str
) -> None:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(json=NOT_A_BOT | {"name": name})
    service = FederationService(ENDPOINT, "token")

    with pytest.raises(FederationException, match="dedicated Federation operator"):
        await service.authenticate()

    assert not service.is_authenticated
    assert service.is_authentication_pending


@pytest.mark.anyio
async def test_an_unconfirmed_bot_configuration_does_not_authenticate(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(json=NOT_A_BOT)
    respx_mock.patch(f"{ENDPOINT}/operators/{OPERATOR['uuid']}/configuration").respond(
        json=NOT_A_BOT["configuration"]
    )
    service = FederationService(ENDPOINT, "token")

    with pytest.raises(FederationException, match="did not confirm"):
        await service.authenticate()

    assert not service.is_authenticated
    assert service.is_authentication_pending


@pytest.mark.anyio
async def test_a_disabled_operator_does_not_authenticate(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{ENDPOINT}/operators/self").respond(json=OPERATOR | {"disabled": True})
    service = FederationService(ENDPOINT, "token")

    await service.authenticate()

    assert not service.is_authenticated
    assert not service.is_authentication_pending

