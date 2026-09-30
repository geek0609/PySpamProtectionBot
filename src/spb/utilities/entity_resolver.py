"""Resolves Federation entity UUIDs into human-readable Telegram HTML.

A resolved entity is rendered as "address (uuid)". Telegram entities use a local user reference
when available: a known username becomes "@username (uuid)", while an identified user without a
username becomes an inline tg://user mention. Resolution failures retain a safe UUID fallback so
display code never hides the underlying identifier.
"""

from pyfederation.records import EntityRecord

from spb.classes.federation_service import FederationService
from spb.classes.managers.user_manager import UserManager
from spb.exceptions import FederationException
from spb.utilities import html_escape


async def resolve(
    federation: FederationService,
    users: UserManager,
    entity_uuid: str | None,
    privacy_mode: bool = False,
) -> str:
    """Resolves an entity UUID through Federation and renders it for Telegram HTML, unless chat
    privacy mode requires retaining only its UUID.

    Returns:
        The rendered address and UUID, or the safe UUID fallback when unresolved or private.
    """
    if entity_uuid is None or not entity_uuid.strip():
        return "Unknown"
    if privacy_mode or not federation.is_available:
        return _uuid_fallback(entity_uuid)

    try:
        entity = await federation.entity(entity_uuid)
    except FederationException:
        return _uuid_fallback(entity_uuid)
    return _display(entity, users) if entity is not None else _uuid_fallback(entity_uuid)


def _display(entity: EntityRecord, users: UserManager) -> str:
    """Renders an already-resolved Federation entity without another Federation request."""
    uuid = entity.uuid
    suffix = f" (<code>{html_escape.escape(uuid)}</code>)" if uuid.strip() else ""
    if entity.host.lower() == "telegram.org":
        telegram_user_id = _telegram_user_id(entity.id)
        if telegram_user_id is not None:
            user = users.get_user(telegram_user_id)
            if user is not None:
                if user.username is not None and user.username.strip():
                    return f"@{html_escape.escape(user.username)}{suffix}"
                return f'<a href="tg://user?id={telegram_user_id}">{telegram_user_id}</a>{suffix}'

    return html_escape.escape(_entity_address(entity)) + suffix


def _entity_address(entity: EntityRecord) -> str:
    if entity.id is None or not entity.id.strip():
        return entity.host
    if not entity.host.strip():
        return entity.id
    return f"{entity.id}@{entity.host}"


def _telegram_user_id(identifier: str | None) -> int | None:
    if identifier is None:
        return None
    try:
        return int(identifier)
    except ValueError:
        return None


def _uuid_fallback(entity_uuid: str) -> str:
    return f"<code>{html_escape.escape(entity_uuid)}</code>" if entity_uuid.strip() else "Unknown"
