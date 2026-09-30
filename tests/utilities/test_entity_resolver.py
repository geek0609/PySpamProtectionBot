import pytest
import respx

from spb.classes.federation_service import FederationService
from spb.classes.managers.user_manager import UserManager
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.user_identity import UserIdentity
from spb.utilities import entity_resolver

pytestmark = pytest.mark.anyio

ENDPOINT = "http://127.0.0.1:1"
UUID = "0192aaaa-0000-7000-8000-000000000002"


def entity(host: str, id: str | None) -> dict[str, object]:
    return {
        "uuid": UUID,
        "hash": "hash",
        "host": host,
        "id": id,
        "whitelisted": False,
        "reputation": 0,
        "created": 1_700_000_000,
        "updated": 1_700_000_000,
    }


@pytest.fixture
def users(template: HandlerContext) -> UserManager:
    return template.managers.users


async def resolve(respx_mock: respx.MockRouter, users: UserManager, record: object) -> str:
    respx_mock.get(f"{ENDPOINT}/entities/{UUID}").respond(json=record)
    return await entity_resolver.resolve(FederationService(ENDPOINT), users, UUID)


async def test_a_known_telegram_user_is_shown_by_username(
    respx_mock: respx.MockRouter, users: UserManager
) -> None:
    users.save_user(UserIdentity(42, "tester", "Tester", None))

    rendered = await resolve(respx_mock, users, entity("telegram.org", "42"))

    assert rendered == f"@tester (<code>{UUID}</code>)"


async def test_a_known_telegram_user_without_a_username_is_mentioned(
    respx_mock: respx.MockRouter, users: UserManager
) -> None:
    users.save_user(UserIdentity(42, None, "Tester", None))

    rendered = await resolve(respx_mock, users, entity("telegram.org", "42"))

    assert rendered == f'<a href="tg://user?id=42">42</a> (<code>{UUID}</code>)'


async def test_any_other_entity_is_shown_by_its_address(
    respx_mock: respx.MockRouter, users: UserManager
) -> None:
    assert await resolve(respx_mock, users, entity("telegram.org", "7")) == (
        f"7@telegram.org (<code>{UUID}</code>)"
    )
    assert await resolve(respx_mock, users, entity("spam.example", None)) == (
        f"spam.example (<code>{UUID}</code>)"
    )


async def test_an_unresolved_entity_keeps_its_uuid(
    respx_mock: respx.MockRouter, users: UserManager
) -> None:
    respx_mock.get(f"{ENDPOINT}/entities/{UUID}").respond(404, json={"error": "not found"})

    assert await entity_resolver.resolve(FederationService(ENDPOINT), users, UUID) == (
        f"<code>{UUID}</code>"
    )


async def test_privacy_mode_and_a_missing_server_keep_only_the_uuid(users: UserManager) -> None:
    unavailable = FederationService.unavailable()

    assert await entity_resolver.resolve(unavailable, users, UUID) == f"<code>{UUID}</code>"
    assert await entity_resolver.resolve(
        FederationService(ENDPOINT), users, UUID, privacy_mode=True
    ) == (f"<code>{UUID}</code>")
    assert await entity_resolver.resolve(unavailable, users, " ") == "Unknown"
