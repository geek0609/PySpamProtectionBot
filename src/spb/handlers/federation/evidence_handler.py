"""The /evidence command handler."""

import logging

from pyrogram.enums import ParseMode
from pyrogram.types import Message, ReplyParameters

from spb.classes.federation_web_links import FederationWebLinks, Record
from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.exceptions import FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.utilities import evidence_renderer, html_escape, message_helper

log = logging.getLogger(__name__)


@update_handler(UpdateType.COMMAND, commands=("evidence",))
class EvidenceHandler(Handler):
    """Looks up an evidence record on the Federation server by UUID and displays it.

    When the caller is an authenticated operator, the request is made using that operator's token.
    In group chats the response is ephemeral so only the caller sees it; in private chats a
    regular message is sent. File attachments associated with the evidence are downloaded from
    Federation and sent as individual documents replying to the evidence info message.
    """

    async def handle(self, context: HandlerContext) -> None:
        message = context.current_update.message
        if message is None or message.from_user is None:
            return

        args = message_helper.parse_arguments(message.text)
        if len(args) != 1 or not message_helper.is_uuid(args[0]):
            lang = self.resolve_language(context, message)
            error = context.languages.get(lang, "evidence", "usage")
            await self.send_html(context, message, error, message_helper.is_group(message.chat))
            return

        await self._handle_evidence_lookup(context, message, args[0])

    async def _handle_evidence_lookup(
        self, context: HandlerContext, message: Message, evidence_uuid: str
    ) -> None:
        """Looks up an evidence record and displays it, followed by its file attachments."""
        lm = context.languages
        lang = self.resolve_language(context, message)
        operator = context.managers.operators.get_operator(message.from_user.id)

        # Confidential evidence is visible only to the operator it belongs to, so the lookup runs as
        # the caller when they are authenticated and as the bot otherwise.
        access_token = operator.access_token if operator is not None else None

        try:
            evidence = await context.federation.evidence_as(access_token, evidence_uuid)
        except FederationException as e:
            log.debug("Failed to fetch evidence %s: %s", evidence_uuid, e)
            await self.send_html(context, message, lm.get(lang, "evidence", "query_error"))
            return

        if evidence is None:
            not_found = lm.get(lang, "evidence", "not_found", html_escape.escape(evidence_uuid))
            await self.send_html(context, message, not_found)
            return

        html = evidence_renderer.info_html(lm, lang, evidence)
        markup = FederationWebLinks.attach(
            None, context.web_links.button(lm, lang, Record.EVIDENCE, evidence.uuid)
        )
        in_group = message_helper.is_group(message.chat)
        info = await self.execute(
            "evidence-ephemeral" if in_group else "evidence-reply",
            context.telegram_client.send_message,
            chat_id=message.chat.id,
            text=html,
            parse_mode=ParseMode.HTML,
            reply_parameters=ReplyParameters(message_id=message.id),
            message_thread_id=message_helper.topic_id(message),
            ephemeral_message_parameters=message_helper.ephemeral_to(
                message.from_user.id if in_group else None
            ),
            reply_markup=markup,
        )

        # The info message is ephemeral in groups, so attachments cannot reply to it there.
        await evidence_renderer.send_attachments(
            context.telegram_client,
            context.federation,
            access_token,
            evidence,
            message.chat.id,
            None if in_group else info.id,
        )
