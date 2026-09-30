"""The buttons on #REPORT_ASSIGNED notifications."""

import logging

from pyfederation.enums import ClassificationFlag
from pyrogram.types import Message

from spb.classes.federation_web_links import FederationWebLinks, Record
from spb.classes.handler import Handler
from spb.classes.notifications.operator_report_callback import CLOSE, PREFIX
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.exceptions import FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.language import Language
from spb.utilities import message_helper

log = logging.getLogger(__name__)

CLAIM_KEY_PREFIX = "operator_report_close:"


@update_handler(UpdateType.CALLBACK_QUERY, callback_data=(f"{PREFIX}:",))
class OperatorReportHandler(Handler):
    """Closes a report from the buttons of its #REPORT_ASSIGNED notification.

    The report is closed as the operator who pressed the button, using their stored credential; it
    never changes the shared client authentication used by update handlers. Federation decides
    whether that operator may close the report.

    The notification text is never rewritten, so the report UUID and details stay available to the
    operator: a successful close removes the buttons and posts the result as a reply, and a failure
    is only reported in an alert, leaving the buttons in place for another attempt. The report is
    looked up before it is closed; one that is already closed or no longer exists only has its
    buttons removed, since the server would reject closing it.
    """

    async def handle(self, context: HandlerContext) -> None:
        callback = context.current_update.callback_query
        assert callback is not None
        message = callback.message
        parts = callback.data.split(":", 2)
        # Notifications are only delivered to the operator's private chat with the bot.
        if message is None or len(parts) != 3 or callback.from_user.id != message.chat.id:
            await self.answer(context, callback)
            return

        lm = context.languages
        lang = self.resolve_language(context, callback)
        _, report_uuid, action = parts
        classification = ClassificationFlag.__members__.get(action)
        if not message_helper.is_uuid(report_uuid) or (action != CLOSE and classification is None):
            await self.answer(context, callback)
            return

        operator = context.managers.operators.get_operator(callback.from_user.id)
        if operator is None:
            not_operator = lm.get(lang, "operator_report", "not_operator")
            await self.answer_alert(context, callback, not_operator)
            return

        if not context.federation.is_available:
            not_configured = lm.get(lang, "operator_report", "federation_not_configured")
            await self.answer_alert(context, callback, not_configured)
            return

        # Guards against Telegram delivering the same press twice, or a double tap, sending two
        # close requests; the second would be rejected after the first had closed the report.
        claim_key = f"{CLAIM_KEY_PREFIX}{callback.from_user.id}:{report_uuid}"
        if context.cache.get_if_present(claim_key) is not None:
            await self.answer(context, callback)
            return
        context.cache.put(claim_key, True)

        # The report may have been closed elsewhere (another operator, the web application), and
        # closing it again would only be rejected, so its current state is checked first.
        try:
            report = await context.federation.report_as(operator.access_token, report_uuid)
        except FederationException as e:
            log.warning(
                "Failed to look up report %s for Telegram operator %s: %s",
                report_uuid,
                callback.from_user.id,
                e,
            )
            context.cache.remove(claim_key)
            lookup_failed = lm.get(lang, "operator_report", "lookup_failed")
            await self.answer_alert(context, callback, lookup_failed)
            return

        if report is None or not report.opened:
            await self.remove_inline_keyboard(context, message)
            key = "not_found" if report is None else "already_closed"
            await self.answer_alert(context, callback, lm.get(lang, "operator_report", key))
            return

        try:
            await context.federation.close_report(
                operator.access_token, report_uuid, classification
            )
        except FederationException as e:
            log.warning(
                "Failed to close report %s for Telegram operator %s: %s",
                report_uuid,
                callback.from_user.id,
                e,
            )
            context.cache.remove(claim_key)
            rejected = lm.get(lang, "operator_report", "federation_rejected_close")
            await self.answer_alert(context, callback, rejected)
            return

        await self.answer(context, callback, lm.get(lang, "notifications", "report.closed"))
        await self._reply_closed(context, message, lang, report_uuid, classification)

    async def _reply_closed(
        self,
        context: HandlerContext,
        message: Message,
        lang: Language,
        report_uuid: str,
        classification: ClassificationFlag | None,
    ) -> None:
        """Marks a notification as handled: removes its buttons and replies to it with the outcome,
        leaving the notification text itself intact."""
        await self.remove_inline_keyboard(context, message)

        lm = context.languages
        html = lm.get(lang, "operator_report", "closed_header", report_uuid)
        if classification is not None:
            html += lm.get(lang, "operator_report", "closed_classification", classification.name)
        markup = FederationWebLinks.attach(
            None, context.web_links.button(lm, lang, Record.REPORT, report_uuid)
        )
        await self.reply_html(context, "operator-report-closed", message, html, markup)
