"""Renders what Federation knows about a Telegram user, as HTML.

The same summary appears in two places, the /info reply and the secretary contact page, and both
must say the same thing about the same person. Keeping the rendering here is what guarantees that,
and stops the secretary handlers from reaching into the federation handlers to borrow it.
"""

import logging
from collections.abc import Mapping

from pyfederation.records import EntityQueryResult, EntityRecord

from spb.classes.language_manager import LanguageManager
from spb.exceptions import FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.language import Language
from spb.utilities import entity_resolver, html_escape, message_helper

log = logging.getLogger(__name__)

TELEGRAM_ENTITY_SUFFIX = "@telegram.org"
"""Federation addresses every Telegram entity under this host."""


async def append_contact_info(
    context: HandlerContext, html: list[str], contact_id: int, lang: Language
) -> str | None:
    """Appends the same Federation information shown by /info for a secretary contact.

    Contact state is rendered by the secretary settings handler alongside this section.

    Returns:
        The contact's entity UUID, or None when Federation has no record of them.
    """
    lm = context.languages
    html.append(lm.get(lang, "info", "header"))
    address = f"{contact_id}{TELEGRAM_ENTITY_SUFFIX}"
    html.append(lm.get(lang, "info", "queried", html_escape.escape(address)) + "\n")

    if not context.federation.is_available:
        html.append(lm.get(lang, "info", "unavailable_no_federation"))
        return None

    entity = await fetch_entity(context, address)
    if entity is None:
        html.append(lm.get(lang, "info", "entity_not_found", html_escape.escape(address)))
        return None

    append_entity_fields(html, context, entity, False, lang)
    html.append(lm.get(lang, "info", "reputation", entity.reputation) + "\n")
    whitelisted = lm.get(lang, "general", "yes" if entity.whitelisted else "no")
    html.append(lm.get(lang, "info", "whitelisted", whitelisted) + "\n")

    if entity.relationship_type is not None or entity.relationship_entity is not None:
        relationship = []
        if entity.relationship_type is not None:
            relationship.append(
                html_escape.escape(message_helper.humanize(entity.relationship_type.name))
            )
        if entity.relationship_entity is not None:
            relationship.append(
                await entity_resolver.resolve(
                    context.federation, context.managers.users, entity.relationship_entity
                )
            )
        html.append(lm.get(lang, "info", "relationship", " ".join(relationship)) + "\n")

    created = message_helper.format_timestamp(entity.created)
    updated = message_helper.format_updated(lm, lang, entity.updated)
    html.append(lm.get(lang, "info", "created", html_escape.escape(created)) + "\n")
    html.append(lm.get(lang, "info", "updated", html_escape.escape(updated)) + "\n")
    append_entity_query(html, await fetch_entity_query(context, address), lm, lang)
    await append_blacklists(context, html, entity.uuid, lm, lang)
    await append_reports(context, html, entity.uuid, lm, lang)
    return entity.uuid


async def fetch_entity(context: HandlerContext, identifier: str) -> EntityRecord | None:
    """Fetches an entity record by UUID, SHA-256 hash or address, or None when the entity does not
    exist or the server cannot answer."""
    try:
        return await context.federation.entity(identifier)
    except FederationException as e:
        log.debug("Failed to fetch entity %s: %s", identifier, e)
        return None


async def fetch_entity_query(context: HandlerContext, identifier: str) -> EntityQueryResult | None:
    """Fetches the server's assessment of an entity by UUID, SHA-256 hash or address, or None when
    the entity does not exist or the query fails."""
    try:
        return await context.federation.query_entity(identifier)
    except FederationException as e:
        log.debug("Failed to query entity %s: %s", identifier, e)
        return None


def append_entity_fields(
    html: list[str],
    context: HandlerContext,
    entity: EntityRecord,
    privacy_mode: bool,
    lang: Language,
) -> None:
    """Appends the entity identity fields.

    The entity address and UUID are always shown, each on their own line. For a Telegram entity
    (<id>@telegram.org) whose user is registered locally, the resolved Telegram username and an
    inline mention are shown alongside. When a Telegram entity cannot be resolved locally, or the
    host is not Telegram, the identity metadata published on the Federation record is used as a
    fallback.

    Args:
        privacy_mode: Whether identity resolution must be suppressed.
    """
    lm = context.languages
    address = _entity_address(entity)
    if address is not None:
        html.append(lm.get(lang, "info", "entity_address", html_escape.escape(address)) + "\n")
    if entity.uuid.strip():
        html.append(lm.get(lang, "info", "entity_uuid", html_escape.escape(entity.uuid)) + "\n")

    if privacy_mode:
        return

    metadata = entity.metadata or {}
    if entity.host.lower() == "telegram.org":
        telegram_user_id = _telegram_user_id(entity.id)
        user = (
            context.managers.users.get_user(telegram_user_id)
            if telegram_user_id is not None
            else None
        )
        if user is not None:
            if user.username is not None and user.username.strip():
                username = "@" + html_escape.escape(user.username)
                html.append(lm.get(lang, "info", "telegram_username", username) + "\n")
            mention = f'<a href="tg://user?id={telegram_user_id}">{telegram_user_id}</a>'
            html.append(lm.get(lang, "info", "telegram_id", mention) + "\n")
            _append_metadata(html, metadata, lm, lang, False)
            operator = context.managers.operators.get_operator(user.user_id)
            if operator is not None:
                operator_uuid = html_escape.escape(operator.operator_uuid)
                html.append(lm.get(lang, "info", "operator", operator_uuid) + "\n")
            return

    _append_metadata(html, metadata, lm, lang, True)


async def append_blacklists(
    context: HandlerContext, html: list[str], entity_uuid: str, lm: LanguageManager, lang: Language
) -> None:
    """Appends the active blacklist records of the entity."""
    try:
        blacklists = await context.federation.active_blacklists(entity_uuid)
    except FederationException as e:
        log.debug("Failed to load blacklists for entity %s: %s", entity_uuid, e)
        return

    if not blacklists:
        return

    html.append(lm.get(lang, "info", "blacklists_header"))
    for blacklist in blacklists:
        html.append(
            lm.get(
                lang,
                "info",
                "blacklist_entry",
                html_escape.escape(message_helper.humanize(blacklist.type.name)),
                html_escape.escape(message_helper.format_timestamp(blacklist.created)),
                html_escape.escape(blacklist.uuid),
            )
        )


async def append_reports(
    context: HandlerContext, html: list[str], entity_uuid: str, lm: LanguageManager, lang: Language
) -> None:
    """Appends up to five recent reports for the entity."""
    try:
        reports = await context.federation.entity_reports(entity_uuid, 5)
    except FederationException as e:
        log.debug("Failed to load reports for entity %s: %s", entity_uuid, e)
        return

    if not reports:
        return

    html.append(lm.get(lang, "info", "reports_header"))
    for report in reports:
        html.append(
            lm.get(
                lang,
                "info",
                "report_entry",
                html_escape.escape(message_helper.humanize(report.incident_type.name)),
                lm.get(lang, "general", "open" if report.opened else "closed"),
                html_escape.escape(message_helper.format_timestamp(report.created)),
                html_escape.escape(report.uuid),
            )
        )


def append_entity_query(
    html: list[str], entity_query: EntityQueryResult | None, lm: LanguageManager, lang: Language
) -> None:
    """Appends the current recommendation and aggregate result counts returned by Federation's
    entity-query endpoint, or that the assessment is unavailable."""
    html.append(lm.get(lang, "info", "assessment_header"))
    if entity_query is None:
        html.append(lm.get(lang, "info", "assessment_status_unavailable"))
        return

    if entity_query.suggested_action is None:
        html.append(lm.get(lang, "info", "assessment_suggested_action_none"))
    else:
        action = html_escape.escape(message_helper.humanize(entity_query.suggested_action.name))
        html.append(lm.get(lang, "info", "assessment_suggested_action", action))

    if entity_query.suggested_lift_timestamp is not None:
        lift = message_helper.format_timestamp(entity_query.suggested_lift_timestamp)
        html.append(lm.get(lang, "info", "assessment_suggested_lift", html_escape.escape(lift)))

    related = len(entity_query.related_entities)
    blacklists = len(entity_query.active_blacklists)
    html.append(lm.get(lang, "info", "assessment_related_entities", related))
    html.append(lm.get(lang, "info", "assessment_active_blacklists", blacklists))


def _append_metadata(
    html: list[str],
    metadata: Mapping[str, object],
    lm: LanguageManager,
    lang: Language,
    include_username: bool,
) -> None:
    """Appends the identity metadata stored on the entity (username, first and last name, and bot
    flag) when present.

    Args:
        include_username: Whether to render the metadata username; suppressed when a resolved
            local Telegram user already supplies an equivalent username field.
    """
    username = metadata.get("username")
    if include_username and username is not None and str(username).strip():
        html.append(lm.get(lang, "info", "username", html_escape.escape(str(username))) + "\n")
    first_name = metadata.get("first_name")
    if first_name is not None and str(first_name).strip():
        html.append(lm.get(lang, "info", "first_name", html_escape.escape(str(first_name))) + "\n")
    last_name = metadata.get("last_name")
    if last_name is not None and str(last_name).strip():
        html.append(lm.get(lang, "info", "last_name", html_escape.escape(str(last_name))) + "\n")
    if "is_bot" in metadata:
        is_bot = lm.get(lang, "general", "yes" if metadata["is_bot"] is True else "no")
        html.append(lm.get(lang, "info", "bot", is_bot) + "\n")


def _entity_address(entity: EntityRecord) -> str | None:
    """Builds the entity address as id@host, omitting whichever part is blank, or None when both
    are."""
    if entity.id is None or not entity.id.strip():
        return entity.host if entity.host.strip() else None
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
