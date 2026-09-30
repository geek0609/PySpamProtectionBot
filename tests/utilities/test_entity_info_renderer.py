from collections.abc import Iterator
from pathlib import Path

import pytest
import respx

from spb.classes.federation_service import FederationService
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.operator_identity import OperatorIdentity
from spb.objects.database.user_identity import UserIdentity
from spb.utilities import entity_info_renderer
from tests.support import contexts

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"
ADDRESS = "42@telegram.org"
UUID = "0192aaaa-0000-7000-8000-000000000002"
OPERATOR = "0192aaaa-0000-7000-8000-000000000003"

ENTITY = {
    "uuid": UUID,
    "hash": "hash",
    "host": "telegram.org",
    "id": "42",
    "metadata": {"first_name": "Tester", "is_bot": False},
    "whitelisted": False,
    "reputation": -3,
    "created": 1_700_000_000,
    "updated": 0,
}


@pytest.fixture
def context(tmp_path: Path) -> Iterator[HandlerContext]:
    context = contexts.template(tmp_path, FederationService(ENDPOINT))
    yield context
    context.database.close()


def mock_contact(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{ENDPOINT}/entities/{ADDRESS}").respond(json=ENTITY)
    respx_mock.get(f"{ENDPOINT}/entities/{ADDRESS}/query").respond(
        json={
            "entity_record": ENTITY,
            "related_entities": [],
            "active_blacklists": [],
            "suggested_action": "CAUTION",
        }
    )
    respx_mock.get(url__startswith=f"{ENDPOINT}/entities/{UUID}/blacklist").respond(
        json=[
            {
                "uuid": "blacklist-uuid",
                "operator": OPERATOR,
                "entity": UUID,
                "type": "SPAM",
                "lifted": False,
                "created": 1_700_000_000,
            }
        ]
    )
    respx_mock.get(url__startswith=f"{ENDPOINT}/entities/{UUID}/reports").respond(json=[])


async def test_a_known_contact_is_rendered_with_what_federation_holds(
    respx_mock: respx.MockRouter, context: HandlerContext
) -> None:
    mock_contact(respx_mock)
    context.managers.users.save_user(UserIdentity(42, "tester", "Tester", None))
    context.managers.operators.save_operator(42, OperatorIdentity(OPERATOR, "token"))
    html: list[str] = []

    uuid = await entity_info_renderer.append_contact_info(
        context, html, 42, context.languages.default_language
    )

    rendered = "".join(html)
    assert uuid == UUID
    assert "<b>Telegram Username:</b> @tester" in rendered
    assert '<a href="tg://user?id=42">42</a>' in rendered
    assert "<b>First Name:</b> Tester" in rendered
    assert "<b>Bot:</b> No" in rendered
    assert f"<b>Operator:</b> <code>{OPERATOR}</code>" in rendered
    assert "<b>Reputation:</b> -3" in rendered
    assert "<b>Suggested Action:</b> Caution" in rendered
    assert "Spam (2023-11-14 22:13:20) <code>blacklist-uuid</code>" in rendered


async def test_an_unknown_contact_is_reported_as_not_found(
    respx_mock: respx.MockRouter, context: HandlerContext
) -> None:
    respx_mock.get(f"{ENDPOINT}/entities/{ADDRESS}").respond(404, json={"error": "not found"})
    html: list[str] = []

    uuid = await entity_info_renderer.append_contact_info(
        context, html, 42, context.languages.default_language
    )

    assert uuid is None
    assert f"No entity matches <code>{ADDRESS}</code>." in "".join(html)
