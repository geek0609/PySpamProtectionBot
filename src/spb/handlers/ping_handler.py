"""The /ping command."""

import logging
import time
from datetime import UTC, datetime

from pyfederation.records import OperatorRecord

from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.exceptions import FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.operator_identity import OperatorIdentity
from spb.objects.language import Language
from spb.utilities import html_escape

log = logging.getLogger(__name__)


@update_handler(UpdateType.COMMAND, commands=("ping",))
class PingHandler(Handler):
    """Answers /ping in every chat the bot answers commands in.

    It is a double check rather than a single one. Answering at all demonstrates the bot itself is
    alive and processing updates, and the reply's own text reports how far behind it is doing so; a
    second, independent check asks the configured Federation server for its own information and
    times that round trip separately, so a Federation outage and a bot outage are never confused
    for one another. When the caller has stored Federation operator credentials of their own, the
    same opportunity verifies those live and reports their standing, exactly as /authinfo would.

    Nothing here is sensitive: the Federation figures shown are the same public counts already on
    the /start screen and the settings menu, and the operator block never carries the operator's
    own access token, only what the server would tell the operator about themselves.
    """

    async def handle(self, context: HandlerContext) -> None:
        message = context.current_update.message
        if message is None or message.from_user is None:
            return

        lm = context.languages
        lang = self.resolve_language(context, message)
        latency = max(0, round((datetime.now(UTC) - message.date).total_seconds() * 1000))

        online = lm.get(lang, "ping", "status_online")
        html = [lm.get(lang, "ping", "bot_status", online)]
        html.append(lm.get(lang, "ping", "bot_latency", latency))
        html.append(await _federation_html(context, lang))

        identity = context.managers.operators.get_operator(message.from_user.id)
        if identity is not None:
            html.append(await _operator_html(context, lang, message.from_user.id, identity))
        await self.send_html(context, message, "".join(html))


async def _federation_html(context: HandlerContext, lang: Language) -> str:
    """Builds the Federation section: whether a server is configured, reachable, and how the bot's
    own token was treated, timed independently of the bot's own latency so a slow or down
    Federation server is never mistaken for a slow or down bot."""
    lm = context.languages
    federation = context.federation

    html = [lm.get(lang, "ping", "federation_header")]

    def status(key: str) -> str:
        return lm.get(lang, "ping", "federation_status", lm.get(lang, "ping", key))

    if not federation.is_available:
        return "".join([*html, status("status_not_configured"), "\n"])

    start = time.monotonic()
    try:
        server = await federation.server_information()
    except FederationException as e:
        log.debug("Federation server unreachable during /ping: %s", e)
        return "".join([*html, status("status_unreachable"), "\n"])
    latency = round((time.monotonic() - start) * 1000)

    access = "access_authenticated" if federation.is_authenticated else "access_anonymous"
    server_name = html_escape.escape(server.name)
    api_version = html_escape.escape(server.api_version)
    html += [
        status("status_available"),
        lm.get(lang, "ping", "federation_access", lm.get(lang, "ping", access)),
        lm.get(lang, "ping", "federation_latency", latency),
        lm.get(lang, "ping", "federation_server", server_name, api_version),
        lm.get(lang, "ping", "federation_stat_entities", server.known_entities or 0),
        lm.get(lang, "ping", "federation_stat_blacklist", server.blacklist_records or 0),
        lm.get(lang, "ping", "federation_stat_reports", server.reports or 0),
        lm.get(lang, "ping", "federation_stat_evidence", server.evidence_records or 0),
        lm.get(lang, "ping", "federation_stat_attachments", server.file_attachment_records or 0),
        lm.get(lang, "ping", "federation_stat_operators", server.operators or 0),
        "\n",
    ]
    return "".join(html)


async def _operator_html(
    context: HandlerContext, lang: Language, user_id: int, identity: OperatorIdentity
) -> str:
    """Builds the operator section, verifying the caller's credential live the same way /authinfo
    does; a credential the server no longer honours is reported as such rather than shown
    stale."""
    lm = context.languages
    html = lm.get(lang, "ping", "operator_header")

    try:
        operator = await context.federation.operator_for(identity.access_token)
    except FederationException as e:
        log.debug(
            "Could not verify operator credential for Telegram user %s during /ping: %s",
            user_id,
            e,
        )
        return html + lm.get(lang, "ping", "operator_unavailable")

    status = "operator_status_disabled" if operator.disabled else "operator_status_active"
    return (
        html
        + lm.get(lang, "ping", "operator_name", html_escape.escape(operator.name))
        + lm.get(lang, "ping", "operator_status", lm.get(lang, "ping", status))
        + lm.get(lang, "ping", "operator_permissions", _permission_summary(context, lang, operator))
    )


def _permission_summary(context: HandlerContext, lang: Language, operator: OperatorRecord) -> str:
    """Lists the operator's granted permissions by name, or says there are none."""
    lm = context.languages
    granted = [
        lm.get(lang, "ping", key)
        for key, held in (
            ("permission_client", operator.client_permissions),
            ("permission_management", operator.management_permissions),
            ("permission_operator", operator.operator_permissions),
        )
        if held
    ]
    return ", ".join(granted) if granted else lm.get(lang, "ping", "permission_none")
