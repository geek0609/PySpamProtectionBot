"""The /link command, for authenticated Federation operators."""

import logging
import re

from pyfederation.enums import EntityRelationshipType
from pyrogram.errors import RPCError
from pyrogram.types import InlineKeyboardButton

from spb.classes.federation_web_links import FederationWebLinks, Record
from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.exceptions import FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.utilities import html_escape, message_helper

log = logging.getLogger(__name__)

TELEGRAM_ENTITY_SUFFIX = "@telegram.org"

_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_NUMERIC = re.compile(r"[0-9]+")
_RELATIONSHIPS = {
    "alternative": EntityRelationshipType.ALTERNATIVE,
    "proxy": EntityRelationshipType.PROXY,
    "child": EntityRelationshipType.CHILD,
}


@update_handler(UpdateType.COMMAND, commands=("link",))
class LinkHandler(Handler):
    """Records a relationship between two Federation entities, as the calling operator.

    /link <entity> <target> <type>, where the entities may be Telegram usernames, user ids,
    mentions, Federation UUIDs, hashes, or entity addresses, and type is one of alternative, proxy,
    or child.
    """

    async def handle(self, context: HandlerContext) -> None:
        message = context.current_update.message
        if message is None or message.from_user is None:
            return

        lm = context.languages
        lang = self.resolve_language(context, message)
        user_id = message.from_user.id

        identity = context.managers.operators.get_operator(user_id)
        if identity is None:
            await self.send_html(context, message, lm.get(lang, "link", "not_authenticated"))
            return

        args = message_helper.parse_arguments(message.text)
        if len(args) < 3:
            await self.send_html(context, message, lm.get(lang, "link", "usage"))
            return

        entity_input, target_input = args[0], args[1]
        relationship = _RELATIONSHIPS.get(args[2].lower())
        if relationship is None:
            invalid_type = lm.get(lang, "link", "invalid_type", html_escape.escape(args[2]))
            await self.send_html(context, message, invalid_type)
            return

        if not context.federation.is_available:
            not_configured = lm.get(lang, "link", "federation_not_configured")
            await self.send_html(context, message, not_configured)
            return

        entity = await _resolve_entity(context, entity_input)
        target = await _resolve_entity(context, target_input)
        if entity is None:
            not_found = lm.get(lang, "link", "entity_not_found", html_escape.escape(entity_input))
            await self.send_html(context, message, not_found)
            return
        if target is None:
            not_found = lm.get(lang, "link", "target_not_found", html_escape.escape(target_input))
            await self.send_html(context, message, not_found)
            return

        try:
            await context.federation.link_entities(
                identity.access_token, entity, target, relationship
            )
        except FederationException as e:
            log.warning("Failed to set entity relationship for operator %s: %s", user_id, e)
            failed = lm.get(lang, "link", "failed", html_escape.escape(str(e)))
            await self.send_html(context, message, failed)
            return

        linked = lm.get(
            lang,
            "link",
            "linked",
            html_escape.escape(entity_input),
            html_escape.escape(target_input),
            relationship.name.lower(),
        )
        links = context.web_links
        target_url = links.url(Record.ENTITY, target)
        await self.send_html(
            context,
            message,
            linked,
            False,
            FederationWebLinks.attach(
                None,
                links.button(lm, lang, Record.ENTITY, entity),
                None
                if target_url is None
                else InlineKeyboardButton(lm.get(lang, "buttons", "view_target"), url=target_url),
            ),
        )


async def _resolve_entity(context: HandlerContext, argument: str) -> str | None:
    """Resolves an identifier to one the Federation server recognises.

    UUIDs, SHA-256 hashes and entity addresses are passed through unchanged. Telegram identifiers
    the bot can recognise on its own, user ids, @username mentions and plain usernames, are
    resolved to the <id>@telegram.org entity address.

    Returns:
        The entity identifier, or None when the argument cannot be resolved.
    """
    if message_helper.is_uuid(argument) or _SHA256.fullmatch(argument):
        return argument
    if argument.startswith("@"):
        return await _resolve_username(context, argument[1:])
    if "@" in argument or "/" in argument or ":" in argument:
        return argument
    if _NUMERIC.fullmatch(argument):
        return f"{argument}{TELEGRAM_ENTITY_SUFFIX}"
    return await _resolve_username(context, argument)


async def _resolve_username(context: HandlerContext, username: str) -> str | None:
    """Resolves a username to its user's entity address, first through the users table and then
    through Telegram, or returns None when no user by that name can be found."""
    normalized = username.strip().removeprefix("@").lower()
    if not normalized:
        return None

    user_id = context.managers.users.get_user_id_by_username(normalized)
    if user_id is None:
        try:
            user_id = (await context.telegram_client.get_chat(f"@{normalized}")).id
        except RPCError as e:
            log.debug("Failed to resolve username @%s through the Telegram API: %s", normalized, e)
            return None
    return f"{user_id}{TELEGRAM_ENTITY_SUFFIX}" if user_id > 0 else None
