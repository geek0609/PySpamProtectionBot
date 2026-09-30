"""The /info command handler."""

import logging
import re

from pyfederation.records import EntityQueryResult, EntityRecord
from pyrogram.enums import MessageEntityType
from pyrogram.errors import RPCError
from pyrogram.parser.utils import add_surrogates, remove_surrogates
from pyrogram.types import Message

from spb.classes.federation_web_links import FederationWebLinks, Record
from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.exceptions import ArgumentParseException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.language import Language
from spb.utilities import entity_info_renderer, entity_resolver, html_escape, message_helper

log = logging.getLogger(__name__)

TELEGRAM_ENTITY_SUFFIX = "@telegram.org"

_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_NUMERIC = re.compile(r"[0-9]+")


@update_handler(UpdateType.COMMAND, commands=("info",))
class InfoHandler(Handler):
    """Looks up a security entity in the Federation database and replies with a summary of the
    entity record, its current Federation recommendation, active blacklists, and recent reports.

    The command can be executed anywhere: in a private chat, in a group or supergroup, or in a
    channel. The target entity can be given directly as an argument, choosing the resolution method
    by its shape:

    - /info <id>: a bare numeric Telegram user id, resolved to the entity address
      <id>@telegram.org,
    - /info <username>: a Telegram username, resolved to the user id via the local users table and,
      as a fallback, Telegram, then searched as <id>@telegram.org. A @username mention in the
      command resolves through its mention entity, which works even when the client does not put
      the raw username in the text,
    - /info <address>: a full entity address (e.g. 123456@telegram.org) searched directly,
    - /info <sha256>: the SHA-256 hex digest of the entity address,
    - /info <uuid>: the entity UUID.

    When the command includes an argument, that explicit target takes precedence. Otherwise, when
    the command is sent as a reply to a message, the target entity is the author of the replied-to
    message. When the command is sent as-is, without a reply and without an argument, the target
    entity is the caller themselves.

    All lookups are best-effort: a missing entity, an unresolvable username, or a Federation server
    error is reported to the user and never propagated.
    """

    async def handle(self, context: HandlerContext) -> None:
        message = context.current_update.message
        if message is None or message.from_user is None:
            return

        log.debug(
            "Processing /info from %s: text='%s', entities=%s",
            message.from_user.id,
            message.text,
            message.entities,
        )

        lm = context.languages
        lang = self.resolve_language(context, message)

        if not context.federation.is_available:
            await self.send_html(
                context, message, lm.get(lang, "info", "unavailable_no_federation")
            )
            return

        try:
            identifier = await _resolve_identifier(context, message, lang)
        except ArgumentParseException as e:
            error = lm.get(lang, "info", "unavailable_error", html_escape.escape(str(e)))
            await self.send_html(context, message, error)
            return

        if identifier is None:
            identifier = f"{message.from_user.id}{TELEGRAM_ENTITY_SUFFIX}"

        entity = await entity_info_renderer.fetch_entity(context, identifier)
        if entity is None:
            not_found = lm.get(lang, "info", "entity_not_found", html_escape.escape(identifier))
            await self.send_html(context, message, not_found)
            return

        entity_query = await entity_info_renderer.fetch_entity_query(context, identifier)
        await self._send_entity_info(context, message, lang, identifier, entity, entity_query)

    async def _send_entity_info(
        self,
        context: HandlerContext,
        message: Message,
        lang: Language,
        display: str,
        entity: EntityRecord,
        entity_query: EntityQueryResult | None,
    ) -> None:
        """Sends the formatted entity summary: the entity record itself, the Federation
        recommendation, its active blacklists, and most recent reports.

        Args:
            display: The identifier the user queried, shown as a reference in the reply.
        """
        lm = context.languages
        html = [lm.get(lang, "info", "header")]
        html.append(lm.get(lang, "info", "queried", html_escape.escape(display)) + "\n")
        configuration = context.managers.chat_configurations.get_chat_configuration(message.chat.id)
        privacy_mode = configuration is not None and configuration.privacy_mode

        entity_info_renderer.append_entity_fields(html, context, entity, privacy_mode, lang)

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
                        context.federation,
                        context.managers.users,
                        entity.relationship_entity,
                        privacy_mode,
                    )
                )
            html.append(lm.get(lang, "info", "relationship", " ".join(relationship)) + "\n")

        created = message_helper.format_timestamp(entity.created)
        updated = message_helper.format_updated(lm, lang, entity.updated)
        html.append(lm.get(lang, "info", "created", html_escape.escape(created)) + "\n")
        html.append(lm.get(lang, "info", "updated", html_escape.escape(updated)) + "\n")

        entity_info_renderer.append_entity_query(html, entity_query, lm, lang)
        await entity_info_renderer.append_blacklists(context, html, entity.uuid, lm, lang)
        await entity_info_renderer.append_reports(context, html, entity.uuid, lm, lang)

        markup = FederationWebLinks.attach(
            None, context.web_links.button(lm, lang, Record.ENTITY, entity.uuid)
        )
        await self.send_html(context, message, "".join(html), markup=markup)


async def _resolve_identifier(
    context: HandlerContext, message: Message, lang: Language
) -> str | None:
    """Resolves the target entity identifier from the message. An explicit command argument takes
    precedence over the implicit author of a replied-to message.

    Returns:
        The resolved identifier, or None when the command has neither an argument nor a reply to a
        message with a known author.
    """
    argument_identifier = await _argument_identifier(context, message, lang)
    if argument_identifier is not None:
        return argument_identifier
    return _reply_identifier(message)


def _reply_identifier(message: Message) -> str | None:
    """Resolves the author of the replied-to message as the target entity.

    A reply to the message's own forum-topic header is treated as no reply: such a message is the
    topic header itself, not a specific target message a user chose to query, so it falls through
    to the caller as the target.
    """
    reply_to = message.reply_to_message
    if reply_to is None or message_helper.is_reply_to_topic_header(message):
        return None
    if reply_to.from_user is None:
        return None
    return f"{reply_to.from_user.id}{TELEGRAM_ENTITY_SUFFIX}"


async def _argument_identifier(
    context: HandlerContext, message: Message, lang: Language
) -> str | None:
    """Resolves the target entity from the command argument, choosing the resolution method by the
    argument's shape, or returns None when the command has no argument."""
    mention = await _mention_identifier(context, message, lang)
    if mention is not None:
        return mention

    argument = message_helper.single_argument(message)
    if argument is None:
        return None
    if message_helper.is_uuid(argument) or _SHA256.fullmatch(argument):
        return argument
    if argument.startswith("@"):
        return await _username_identifier(context, argument[1:], lang)
    if "@" in argument:
        return argument
    if _NUMERIC.fullmatch(argument):
        return f"{argument}{TELEGRAM_ENTITY_SUFFIX}"
    return await _username_identifier(context, argument, lang)


async def _mention_identifier(
    context: HandlerContext, message: Message, lang: Language
) -> str | None:
    """Resolves the target from a Telegram mention entity in the command's argument region, so
    /info @username resolves regardless of how the client rendered the mention text.

    A text mention entity carries the mentioned user directly, giving the user id without any
    lookup. A plain mention entity covers the @username text, whose exact spelling is taken from
    the message text using the entity's offset and length, which count UTF-16 code units.
    """
    if not message.entities or message.text is None:
        return None

    text = add_surrogates(message.text)
    argument_start = message_helper.first_whitespace(text)
    if argument_start < 0:
        return None

    for entity in message.entities:
        if entity.offset <= argument_start:
            continue

        if entity.type is MessageEntityType.TEXT_MENTION and entity.user is not None:
            return f"{entity.user.id}{TELEGRAM_ENTITY_SUFFIX}"

        if entity.type is MessageEntityType.MENTION:
            end = entity.offset + entity.length
            if end <= len(text):
                mention = remove_surrogates(text[entity.offset : end])
                if mention.startswith("@"):
                    return await _username_identifier(context, mention[1:], lang)

    return None


async def _username_identifier(context: HandlerContext, username: str, lang: Language) -> str:
    """Resolves a Telegram username to the entity address <id>@telegram.org, first through the
    local users table and then, as a fallback, through Telegram.

    Raises:
        ArgumentParseException: If the username is empty or cannot be resolved.
    """
    lm = context.languages
    normalized = username.strip().removeprefix("@").lower()
    if not normalized:
        raise ArgumentParseException(lm.get(lang, "info", "invalid_username"))

    user_id = context.managers.users.get_user_id_by_username(normalized)
    if user_id is None:
        user_id = await _resolve_username_from_telegram(context, normalized)
    if user_id is None:
        raise ArgumentParseException(lm.get(lang, "info", "username_not_resolved", normalized))

    return f"{user_id}{TELEGRAM_ENTITY_SUFFIX}"


async def _resolve_username_from_telegram(context: HandlerContext, username: str) -> int | None:
    """Resolves a Telegram username to its user id through Telegram, or None when it cannot."""
    try:
        chat = await context.telegram_client.get_chat(f"@{username}")
    except RPCError as e:
        log.debug("Failed to resolve username @%s through the Telegram API: %s", username, e)
        return None
    return chat.id
