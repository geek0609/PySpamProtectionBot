"""The /blacklist command, for authenticated Federation operators."""

import logging
import re
import time
from dataclasses import dataclass

from pyfederation.enums import IncidentType
from pyrogram.errors import RPCError
from pyrogram.types import InlineKeyboardMarkup, Message

from spb.classes import report_submission_service
from spb.classes.federation_web_links import FederationWebLinks, Record
from spb.classes.handler import Handler
from spb.classes.language_manager import LanguageManager
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.exceptions import ArgumentParseException, FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.context.report_context import ReportContext
from spb.objects.language import Language
from spb.utilities import (
    entity_publisher,
    flat_metadata,
    html_escape,
    incident_types,
    message_content,
    message_helper,
    report_attachments,
)

log = logging.getLogger(__name__)

TELEGRAM_ENTITY_SUFFIX = "@telegram.org"

_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_NUMERIC = re.compile(r"[0-9]+")
_EXPIRES = re.compile(r"(\d+)([smhd])?", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


@dataclass(frozen=True, slots=True)
class _BlacklistRequest:
    """The fully resolved arguments of a blacklist operation.

    Attributes:
        entity_identifier: The entity to blacklist.
        target: The replied-to message whose author is blacklisted, or None for an explicit
            identifier.
        report_uuid: The supporting report, or None to create one for a replied-to author and to
            blacklist an explicit identifier without one.
        incident_type: The incident type of the blacklist.
        expiration_seconds: How long the blacklist lasts, counted from now.
    """

    entity_identifier: str
    target: Message | None
    report_uuid: str | None
    incident_type: IncidentType
    expiration_seconds: int


@update_handler(UpdateType.COMMAND, commands=("blacklist",))
class BlacklistHandler(Handler):
    """Blacklists an entity on the Federation server, optionally backed by a supporting report.

    The command accepts several forms:

    - /blacklist <identifier> <type> <expires> without a reply: blacklist the given entity without
      a report,
    - /blacklist <identifier> <report_uuid> <type> <expires>: blacklist the given entity against an
      existing report,
    - /blacklist <report_uuid> <type> <expires> as a reply to a message: blacklist the replied-to
      message's author against an existing report,
    - /blacklist <type> <expires> as a reply to a message: create a supporting report for the
      replied-to message's author first (exactly like /report, but without a moderator
      notification), then blacklist the author against that freshly created report.

    The expires argument is a duration with a unit suffix (s, m, h or d; plain numbers are
    seconds), such as 30d, 12h or 45m. The blacklist expires that long from now.

    Only a Telegram user with stored operator credentials (/auth) may invoke the command. The
    Federation calls run as the calling operator rather than as the bot, so the blacklist and any
    reference report are attributed to that operator.
    """

    async def handle(self, context: HandlerContext) -> None:
        message = context.current_update.message
        if message is None or message.from_user is None:
            return

        lm = context.languages
        lang = self.resolve_language(context, message)

        async def reply(html: str, markup: InlineKeyboardMarkup | None = None) -> None:
            await self.send_html(context, message, html, True, markup)

        if not context.federation.is_available:
            await reply(lm.get(lang, "blacklist", "federation_not_configured"))
            return

        operator = context.managers.operators.get_operator(message.from_user.id)
        if operator is None:
            await reply(lm.get(lang, "blacklist", "operator_only"))
            return

        try:
            parsed = await _parse_arguments(context, message, lang)
        except ArgumentParseException as e:
            await reply(str(e))
            return

        await _publish_target(context, message, parsed)

        report_uuid = parsed.report_uuid
        if report_uuid is None and parsed.target is not None:
            report_uuid = await _create_reference_report(
                context, operator.access_token, message, parsed.target, parsed.incident_type
            )
            if report_uuid is None:
                await reply(lm.get(lang, "blacklist", "report_failed"))
                return

        expires = int(time.time()) + parsed.expiration_seconds
        try:
            blacklist_uuid = await context.federation.blacklist_entity(
                operator.access_token,
                parsed.entity_identifier,
                report_uuid,
                parsed.incident_type,
                expires,
            )
        except FederationException as e:
            log.warning(
                "Federation rejected blacklisting of %s for operator %s: %s",
                parsed.entity_identifier,
                message.from_user.id,
                e,
            )
            await reply(lm.get(lang, "blacklist", "blacklist_failed"))
            return

        incident_type = message_helper.display_name(parsed.incident_type)
        expiry = message_helper.format_timestamp(expires)
        report_line = (
            lm.get(lang, "blacklist", "report_id", html_escape.escape(report_uuid)) + "\n"
            if report_uuid is not None
            else ""
        )
        html = (
            lm.get(lang, "blacklist", "success_header")
            + lm.get(lang, "blacklist", "entity", html_escape.escape(parsed.entity_identifier))
            + "\n"
            + report_line
            + lm.get(lang, "blacklist", "type", html_escape.escape(incident_type))
            + "\n"
            + lm.get(lang, "blacklist", "expires", html_escape.escape(expiry))
            + "\n"
            + lm.get(lang, "blacklist", "blacklist_id", html_escape.escape(blacklist_uuid))
        )
        web_links = context.web_links
        await reply(
            html,
            FederationWebLinks.attach(
                None,
                web_links.button(lm, lang, Record.BLACKLIST, blacklist_uuid),
                web_links.button(lm, lang, Record.REPORT, report_uuid),
            ),
        )


def telegram_id(entity_identifier: str) -> int | None:
    """Returns the Telegram id of a <id>@telegram.org address, or None when the identifier is not
    a Telegram address."""
    local, _, host = entity_identifier.rpartition("@")
    if f"@{host}" != TELEGRAM_ENTITY_SUFFIX:
        return None
    try:
        return int(local)
    except ValueError:
        return None


async def _create_reference_report(
    context: HandlerContext,
    access_token: str,
    message: Message,
    target: Message,
    incident_type: IncidentType,
) -> str | None:
    """Files the report a blacklist of the replied-to message's author is backed by, as the
    operator."""
    report = ReportContext(
        hash="",
        reporter_id=message.from_user.id,
        chat_id=message.chat.id,
        target_message_id=target.id,
        target_author_id=target.from_user.id,
        target_text=message_content.text_or_caption(target),
        attachments=report_attachments.for_report(context, target),
        target_metadata=flat_metadata.of(target),
        ephemeral=True,
        message_thread_id=message_helper.topic_id(target),
        incident_type=incident_type,
        reporter_is_admin=True,
    )
    return await report_submission_service.submit_operator_reference(context, access_token, report)


async def _parse_arguments(
    context: HandlerContext, message: Message, lang: Language
) -> _BlacklistRequest:
    """Parses and validates the /blacklist arguments, resolving the entity and the report from the
    explicit arguments or the replied-to message.

    Raises:
        ArgumentParseException: If the arguments are invalid, carrying the message to answer with.
    """
    lm = context.languages
    args = message_helper.parse_arguments(message.text)
    if not 2 <= len(args) <= 4:
        raise ArgumentParseException(lm.get(lang, "blacklist", "usage"))

    incident_type = incident_types.parse(args[-2])
    if incident_type is None:
        raise ArgumentParseException(lm.get(lang, "blacklist", "invalid_type", args[-2]))

    expiration_seconds = _parse_expiration_seconds(args[-1])
    if expiration_seconds is None:
        raise ArgumentParseException(lm.get(lang, "blacklist", "invalid_expires", args[-1]))

    if len(args) == 4:
        entity_identifier = await _resolve_identifier(context, args[0])
        if entity_identifier is None:
            raise ArgumentParseException(lm.get(lang, "blacklist", "usage"))
        report_uuid = _require_report_uuid(lm, lang, args[1])
        return _BlacklistRequest(
            entity_identifier, None, report_uuid, incident_type, expiration_seconds
        )

    if len(args) == 3 and _reply_target(message) is None:
        entity_identifier = await _resolve_identifier(context, args[0])
        if entity_identifier is None:
            raise ArgumentParseException(lm.get(lang, "blacklist", "usage"))
        return _BlacklistRequest(entity_identifier, None, None, incident_type, expiration_seconds)

    target = _require_reply_target(context, message, lang)
    report = _require_report_uuid(lm, lang, args[0]) if len(args) == 3 else None
    return _BlacklistRequest(
        f"{target.from_user.id}{TELEGRAM_ENTITY_SUFFIX}",
        target,
        report,
        incident_type,
        expiration_seconds,
    )


def _require_report_uuid(lm: LanguageManager, lang: Language, argument: str) -> str:
    if not message_helper.is_uuid(argument):
        raise ArgumentParseException(lm.get(lang, "blacklist", "invalid_report", argument))
    return argument


async def _publish_target(
    context: HandlerContext, message: Message, parsed: _BlacklistRequest
) -> None:
    """Publishes the blacklist target to Federation, since neither the supporting report nor the
    blacklist can be created for an entity the server has never seen.

    A replied-to author is published with their properties unless the chat has privacy mode on. An
    explicitly named Telegram entity is published by identifier alone, since nothing more is known
    about it here; any other identifier (a UUID, hash, or foreign address) is left to the server as
    given.
    """
    author = parsed.target.from_user if parsed.target is not None else None
    target_id = author.id if author is not None else telegram_id(parsed.entity_identifier)
    if target_id is None:
        return

    try:
        await entity_publisher.publish(context, message.chat.id, target_id, author)
    except FederationException as e:
        # Not fatal on its own: the report or blacklist that follows reports the real failure.
        log.warning("Failed to publish blacklist target %s: %s", parsed.entity_identifier, e)


def _require_reply_target(context: HandlerContext, message: Message, lang: Language) -> Message:
    """Returns the message a blacklist command replies to, once it may be blacklisted.

    Raises:
        ArgumentParseException: If the command is not a reply in a group to another member's
            message, or the author is one of the chat's moderators.
    """
    lm = context.languages
    if not message_helper.is_group(message.chat):
        raise ArgumentParseException(lm.get(lang, "blacklist", "usage"))

    target = _reply_target(message)
    if target is None:
        cannot = lm.get(lang, "blacklist", "cannot_be_reported")
        raise ArgumentParseException(
            Handler.with_regular_group_reply_note(context, lang, message, "blacklist", cannot)
        )
    if target.from_user is None:
        raise ArgumentParseException(lm.get(lang, "blacklist", "cannot_be_reported"))
    if target.from_user.id == message.from_user.id:
        raise ArgumentParseException(lm.get(lang, "blacklist", "self_report"))
    if Handler.is_chat_administrator(context, message.chat.id, target.from_user.id):
        raise ArgumentParseException(lm.get(lang, "blacklist", "admin_report"))
    return target


def _reply_target(message: Message) -> Message | None:
    """The message replied to, or None when the command replies to nothing but its topic."""
    if message.reply_to_message is None or message_helper.is_reply_to_topic_header(message):
        return None
    return message.reply_to_message


async def _resolve_identifier(context: HandlerContext, argument: str) -> str | None:
    """Resolves an identifier argument by its shape: a UUID or hash as given, a username through
    the users table or Telegram, a numeric Telegram id as its address, any other address as
    given."""
    if message_helper.is_uuid(argument) or _SHA256.fullmatch(argument):
        return argument
    if argument.startswith("@"):
        return await _resolve_username(context, argument[1:])
    if "@" in argument:
        return argument
    if _NUMERIC.fullmatch(argument):
        return f"{argument}{TELEGRAM_ENTITY_SUFFIX}"
    return await _resolve_username(context, argument)


async def _resolve_username(context: HandlerContext, username: str) -> str | None:
    """Resolves a username to its Telegram entity address, first through the users table and then
    through Telegram, or returns None when it cannot."""
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
    return f"{user_id}{TELEGRAM_ENTITY_SUFFIX}"


def _parse_expiration_seconds(token: str) -> int | None:
    """Parses a duration such as 30d, 12h, 45m or 90 (seconds), or returns None when it is not a
    positive duration."""
    match = _EXPIRES.fullmatch(token.strip())
    if match is None:
        return None
    seconds = int(match.group(1)) * _UNIT_SECONDS[(match.group(2) or "s").lower()]
    return seconds if seconds > 0 else None
