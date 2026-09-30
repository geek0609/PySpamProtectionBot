"""The /report command and its dialog."""

import logging

from pyfederation.enums import IncidentType
from pyfederation.records import EvidenceRecord, ReportRecord
from pyrogram.enums import ParseMode
from pyrogram.errors import RPCError
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginUser,
    ReplyParameters,
)

from spb.classes import report_submission_service
from spb.classes.federation_web_links import FederationWebLinks, Record
from spb.classes.handler import Handler
from spb.classes.language_manager import LanguageManager
from spb.classes.update_handler import update_handler
from spb.enums import ReportPage, UpdateType
from spb.exceptions import FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.context.report_context import ReportContext
from spb.objects.language import Language
from spb.utilities import (
    flat_metadata,
    html_escape,
    incident_types,
    message_content,
    message_helper,
    report_attachments,
)

log = logging.getLogger(__name__)

CALLBACK_PREFIX = "report"


@update_handler(
    UpdateType.MESSAGE,
    UpdateType.CALLBACK_QUERY,
    callback_data=(f"{CALLBACK_PREFIX}:",),
    priority=50,
)
class ReportHandler(Handler):
    """Lets members report a message to the Federation server.

    /report as a reply to a message opens a two-step dialog: choose the incident type, then either
    submit or reply to the prompt with a comment. /report <type> [comment] submits at once, and
    /report <uuid> shows a report. A message forwarded to the bot privately is reported the same
    way.

    Callbacks carry the report session hash and an action: selecting an incident type, submitting
    the report, or cancelling the dialog. The handler validates that the callback came from the
    session owner and edits the prompt accordingly. Both ephemeral group prompts and regular
    private-chat prompts are supported.

    Validation and dialog prompts in groups are sent as ephemeral messages visible only to the
    reporter.
    """

    def accepts(self, context: HandlerContext) -> bool:
        update = context.current_update
        if update.callback_query is not None:
            return True

        # A message reaches this handler three ways: it invokes /report, it answers the comment
        # prompt of an open dialog, or it is a message forwarded into a private chat to report.
        # Only the first is a command, so the match cannot be expressed by the declaration alone.
        message = update.message
        if message is None or message.from_user is None:
            return False

        return (
            _is_reply_to_active_report_prompt(context, message)
            or context.command_name == "report"
            or _is_private_report_forward(message)
        )

    async def handle(self, context: HandlerContext) -> None:
        update = context.current_update
        if update.callback_query is not None:
            await self._handle_callback(context, update.callback_query)
            return

        message = update.message
        if message is None or message.from_user is None:
            return

        if await self._handle_reply_to_prompt(context, message):
            return

        if context.command_name == "report":
            args = message_helper.parse_arguments(message.text)
            if len(args) == 1 and message_helper.is_uuid(args[0]):
                await self._handle_report_lookup(context, message, args[0])
                return
            await self._handle_report_command(context, message)
            return

        if _is_private_report_forward(message):
            await self._handle_private_forward_report(context, message)

    async def _handle_callback(
        self, context: HandlerContext, callback_query: CallbackQuery
    ) -> None:
        parts = callback_query.data.split(":", 2)
        if parts[1:2] == ["dismiss"]:
            await self._handle_dismiss(context, callback_query)
            return

        message = callback_query.message
        if len(parts) != 3 or message is None:
            await self.answer(context, callback_query)
            return

        _, hash, action = parts
        sessions = context.sessions.report
        lm = context.languages
        if action in ("cancel", "submit"):
            session = sessions.take_owned(hash, callback_query.from_user.id)
            if session is None:
                await self.answer(context, callback_query)
                await _edit_to_report_expired(context, callback_query)
                return
            if action == "cancel":
                lang = self.resolve_language(context, callback_query)
                await _edit_prompt(
                    context,
                    message,
                    session,
                    lm.get(lang, "report", "report_cancelled"),
                    report_submission_service.dismiss_markup(lm, lang),
                    ParseMode.HTML,
                )
            else:
                await report_submission_service.submit(context, session, None)
            await self.answer(context, callback_query)
        elif action.startswith("type:"):
            found = sessions.find(hash)
            if found is None or found.reporter_id != callback_query.from_user.id:
                await self.answer(context, callback_query)
                await _edit_to_report_expired(context, callback_query)
                return
            await self._handle_type_selection(context, callback_query, found, message, action)
        else:
            await self.answer(context, callback_query)

    async def _handle_reply_to_prompt(self, context: HandlerContext, message: Message) -> bool:
        """Takes a reply to an open dialog's comment prompt as the report's comment.

        Returns:
            True when the reply was consumed.
        """
        reply_to = message.reply_to_message
        if reply_to is None:
            return False

        sessions = context.sessions.report
        prompt_message_id = _prompt_message_id(reply_to)
        session = sessions.find_by_prompt_message_id(message.chat.id, prompt_message_id)
        if session is None or session.reporter_id != message.from_user.id:
            return False

        lm = context.languages
        if session.page is not ReportPage.COMMENT:
            not_accepting = lm.get(
                self.resolve_language(context, message), "report", "dialog_no_longer_accepting"
            )
            await self.send_ephemeral_or_regular(context, message, not_accepting, session.ephemeral)
            return True

        comment = message.text
        if not comment or not comment.strip():
            empty = lm.get(self.resolve_language(context, message), "report", "empty_comment")
            await self.send_ephemeral_or_regular(context, message, empty, session.ephemeral)
            return True

        taken = sessions.take_owned_by_prompt_message_id(
            message.chat.id, prompt_message_id, message.from_user.id
        )
        if taken is None:
            return False

        await report_submission_service.submit(context, taken, comment)
        await self.delete_command_message(context, message)
        return True

    async def _handle_report_command(self, context: HandlerContext, message: Message) -> None:
        """Handles a /report command replying to the message to report."""
        if not message_helper.is_group(message.chat):
            return

        configuration = context.managers.chat_configurations.resolve(message.chat.id)
        if not configuration.enabled or not configuration.reporting_enabled:
            return

        lm = context.languages
        lang = self.resolve_language(context, message)

        async def refuse(key: str) -> None:
            await self.send_html(context, message, lm.get(lang, "report", key), ephemeral=True)

        if not context.federation.is_available:
            await refuse("federation_not_configured")
            return

        # /report always submits as the bot itself, which the server refuses unconditionally
        # without client permissions, regardless of what it allows anonymously.
        if not context.federation.is_authenticated:
            await refuse("federation_not_authorized")
            return

        target = message.reply_to_message
        if target is None:
            usage = self.with_regular_group_reply_note(
                context, lang, message, "report", lm.get(lang, "report", "usage")
            )
            await self.send_html(context, message, usage, ephemeral=True)
            return

        if message_helper.is_reply_to_topic_header(message):
            await refuse("usage")
            return
        if target.from_user is None:
            await refuse("cannot_be_reported")
            return
        if target.from_user.id == message.from_user.id:
            await refuse("self_report")
            return
        if self.is_chat_administrator(context, message.chat.id, target.from_user.id):
            await refuse("admin_report")
            return

        args = message_helper.parse_arguments(message.text)
        if not args:
            await self._start_dialog(context, message, target)
            return

        # The first argument names the incident type when it is one; otherwise the whole argument
        # list is the reporter's comment and the report is filed as spam.
        incident_type = incident_types.parse(args[0])
        comment = (" ".join(args[1:]) or None) if incident_type is not None else " ".join(args)
        await self._submit_direct_report(
            context, message, target, incident_type or IncidentType.SPAM, comment
        )

    async def _handle_report_lookup(
        self, context: HandlerContext, message: Message, report_uuid: str
    ) -> None:
        """Shows a report of the Federation server, ephemerally in groups."""
        lm = context.languages
        lang = self.resolve_language(context, message)
        try:
            report = await context.federation.report(report_uuid)
        except FederationException as e:
            log.debug("Failed to fetch report %s: %s", report_uuid, e)
            await self.send_html(context, message, lm.get(lang, "report", "query_error"))
            return
        if report is None:
            not_found = lm.get(lang, "report", "not_found", html_escape.escape(report_uuid))
            await self.send_html(context, message, not_found)
            return

        try:
            evidence = await context.federation.report_evidence(report_uuid, 10)
        except FederationException as e:
            log.debug("Failed to fetch evidence for report %s: %s", report_uuid, e)
            evidence = []

        html = _build_report_info_html(lm, lang, report, evidence)
        markup = FederationWebLinks.attach(
            None, context.web_links.button(lm, lang, Record.REPORT, report.uuid)
        )
        in_group = message_helper.is_group(message.chat)
        await self.send_html(context, message, html, in_group, markup)

    async def _handle_private_forward_report(
        self, context: HandlerContext, message: Message
    ) -> None:
        """Opens a report dialog for a message forwarded to the bot privately."""
        lm = context.languages
        lang = self.resolve_language(context, message)
        if not context.federation.is_available:
            await self.send_html(
                context, message, lm.get(lang, "report", "federation_not_configured")
            )
            return

        # A forwarded message is reported as the bot itself, same as /report.
        if not context.federation.is_authenticated:
            await self.send_html(
                context, message, lm.get(lang, "report", "federation_not_authorized")
            )
            return

        author_id = _forward_author_id(message)
        if author_id is None:
            await self.send_html(context, message, lm.get(lang, "report", "hidden_author_forward"))
            return
        if author_id == message.from_user.id:
            await self.send_html(context, message, lm.get(lang, "report", "self_report"))
            return

        origin = message.forward_origin
        target_message_id = (
            origin.message_id
            if isinstance(origin, MessageOriginChannel) and origin.message_id is not None
            else message.id
        )
        reporter_is_admin = self.is_chat_administrator(
            context, message.chat.id, message.from_user.id
        )
        session = context.sessions.report.create_from_forward(
            message,
            target_message_id,
            author_id,
            report_attachments.for_report(context, message),
            reporter_is_admin,
        )
        await send_private_prompt(context, session, lang, None)

    async def _start_dialog(
        self, context: HandlerContext, message: Message, target: Message
    ) -> None:
        """Opens the two-step report dialog as an ephemeral prompt only the reporter sees."""
        lm = context.languages
        lang = self.resolve_language(context, message)
        reporter_is_admin = self.is_chat_administrator(
            context, message.chat.id, message.from_user.id
        )
        session = context.sessions.report.create(
            message,
            target,
            message_helper.topic_id(target),
            report_attachments.for_report(context, target),
            reporter_is_admin,
            True,
        )

        sent = await context.telegram_client.send_message(
            message.chat.id,
            lm.get(lang, "report", "incident_prompt"),
            parse_mode=ParseMode.DISABLED,
            message_thread_id=session.message_thread_id,
            ephemeral_message_parameters=message_helper.ephemeral_to(message.from_user.id),
            reply_markup=await _build_incident_type_markup(context, lang, session),
        )
        if sent.ephemeral_message_id is not None:
            context.sessions.report.update_prompt_message_id(
                session.hash, sent.ephemeral_message_id
            )
        await self.delete_command_message(context, message)

    async def _submit_direct_report(
        self,
        context: HandlerContext,
        message: Message,
        target: Message,
        incident_type: IncidentType,
        comment: str | None,
    ) -> None:
        """Submits a report at once from the command arguments."""
        session = ReportContext(
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
            reporter_is_admin=self.is_chat_administrator(
                context, message.chat.id, message.from_user.id
            ),
        )

        await self.delete_command_message(context, message)
        await report_submission_service.submit(context, session, comment)

    async def _handle_dismiss(self, context: HandlerContext, callback_query: CallbackQuery) -> None:
        """Deletes the message carrying the Dismiss button."""
        message = callback_query.message
        if message is not None:
            client = context.telegram_client
            try:
                if message.ephemeral_message_id is not None:
                    await client.delete_ephemeral_message(
                        chat_id=message.chat.id,
                        receiver_user_id=callback_query.from_user.id,
                        ephemeral_message_id=message.ephemeral_message_id,
                    )
                else:
                    await client.delete_messages(message.chat.id, message.id)
            except RPCError as e:
                log.debug("Could not dismiss report summary in chat %s: %s", message.chat.id, e)
        await self.answer(context, callback_query)

    async def _handle_type_selection(
        self,
        context: HandlerContext,
        callback_query: CallbackQuery,
        session: ReportContext,
        message: Message,
        action: str,
    ) -> None:
        """Applies the selected incident type and advances the dialog to the comment page."""
        type_name = action.removeprefix("type:")
        try:
            incident_type = IncidentType[type_name]
        except KeyError:
            log.warning("Unknown incident type '%s' in report callback", type_name)
            await self.answer(context, callback_query)
            return

        updated = context.sessions.report.update_incident_type(session.hash, incident_type)
        if updated is None:
            await self.answer(context, callback_query)
            await _edit_to_report_expired(context, callback_query)
            return

        lm = context.languages
        lang = self.resolve_language(context, callback_query)
        markup = message_helper.single_row_markup(
            message_helper.button(
                lm.get(lang, "buttons", "submit_report"),
                f"{CALLBACK_PREFIX}:{updated.hash}:submit",
            ),
            message_helper.button(
                lm.get(lang, "general", "cancel"), f"{CALLBACK_PREFIX}:{updated.hash}:cancel"
            ),
        )
        comment_prompt = lm.get(lang, "report", "comment_prompt")
        await _edit_prompt(context, message, updated, comment_prompt, markup, ParseMode.DISABLED)
        await self.answer(context, callback_query)


async def send_private_prompt(
    context: HandlerContext,
    session: ReportContext,
    lang: Language,
    reply_to_message_id: int | None,
) -> None:
    """Opens a report dialog in the reporter's private chat with the bot by sending its incident
    type prompt; the dialog then continues through this handler's callbacks and replies.

    Args:
        session: The dialog to open, whose chat is the reporter's private chat.
        lang: The language of the prompt.
        reply_to_message_id: The message the prompt answers, or None.
    """
    sent = await context.telegram_client.send_message(
        session.chat_id,
        context.languages.get(lang, "report", "incident_prompt"),
        parse_mode=ParseMode.DISABLED,
        reply_parameters=(
            ReplyParameters(message_id=reply_to_message_id)
            if reply_to_message_id is not None
            else None
        ),
        reply_markup=await _build_incident_type_markup(context, lang, session),
    )
    context.sessions.report.update_prompt_message_id(session.hash, sent.id)


def _prompt_message_id(prompt: Message) -> int:
    """The id a dialog knows its prompt by: the ephemeral message id for an ephemeral prompt."""
    return prompt.ephemeral_message_id if prompt.ephemeral_message_id is not None else prompt.id


def _is_reply_to_active_report_prompt(context: HandlerContext, message: Message) -> bool:
    reply_to = message.reply_to_message
    if reply_to is None:
        return False
    prompt_message_id = _prompt_message_id(reply_to)
    return (
        context.sessions.report.find_by_prompt_message_id(message.chat.id, prompt_message_id)
        is not None
    )


def _is_private_report_forward(message: Message) -> bool:
    """Whether the message is a forward sent to the bot privately, whether or not its original
    author can be resolved.

    Forwards hiding their origin and forwards of the reporter's own messages still qualify, so the
    handler can explain why the report is not possible instead of silently ignoring the message.
    """
    return (
        message_helper.is_private(message.chat)
        and message.from_user is not None
        and message.forward_origin is not None
    )


def _forward_author_id(message: Message) -> int | None:
    """The original author of a forwarded message, a user or a chat, or None when it is hidden."""
    origin = message.forward_origin
    if isinstance(origin, MessageOriginUser):
        return origin.sender_user.id
    if isinstance(origin, MessageOriginChannel):
        return origin.chat.id
    if isinstance(origin, MessageOriginChat):
        return origin.sender_chat.id
    return None


async def _build_incident_type_markup(
    context: HandlerContext, lang: Language, session: ReportContext
) -> InlineKeyboardMarkup:
    """Offers every incident type the server accepts on its own row, and a way to cancel."""
    lm = context.languages
    # A server that declines illegal content rejects ILLEGAL_CONTENT reports, so it is not offered
    illegal_content_allowed = await report_submission_service.is_illegal_content_allowed(context)
    rows = [
        [
            message_helper.button(
                message_helper.display_name(incident_type),
                f"{CALLBACK_PREFIX}:{session.hash}:type:{incident_type.name}",
            )
        ]
        for incident_type in IncidentType
        if incident_type is not IncidentType.ILLEGAL_CONTENT or illegal_content_allowed
    ]
    cancel = message_helper.button(
        lm.get(lang, "general", "cancel"), f"{CALLBACK_PREFIX}:{session.hash}:cancel"
    )
    return InlineKeyboardMarkup([*rows, [cancel]])


async def _edit_prompt(
    context: HandlerContext,
    message: Message,
    session: ReportContext,
    text: str,
    markup: InlineKeyboardMarkup | None,
    parse_mode: ParseMode,
) -> None:
    """Edits the dialog prompt to show new text and keyboard."""
    client = context.telegram_client
    if session.ephemeral:
        if message.ephemeral_message_id is None:
            return
        await client.edit_ephemeral_message_text(
            chat_id=session.chat_id,
            receiver_user_id=session.reporter_id,
            ephemeral_message_id=message.ephemeral_message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=markup,
        )
        return

    await client.edit_message_text(
        chat_id=session.chat_id,
        message_id=message.id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=markup,
    )


async def _edit_to_report_expired(context: HandlerContext, callback_query: CallbackQuery) -> None:
    """Edits the prompt to say that the dialog has expired."""
    message = callback_query.message
    if message is None:
        return

    lm = context.languages
    lang = Handler.resolve_language(context, callback_query)
    markup = report_submission_service.dismiss_markup(lm, lang)
    await Handler.edit_message(
        context, message, callback_query, lm.get(lang, "report", "expired"), markup
    )


def _build_report_info_html(
    lm: LanguageManager, lang: Language, report: ReportRecord, evidence: list[EvidenceRecord]
) -> str:
    """Summarises a Federation report and lists its evidence."""
    html = [lm.get(lang, "report", "header")]

    def line(key: str, *args: object) -> None:
        html.append(lm.get(lang, "report", key, *args) + "\n")

    line("report_id", html_escape.escape(report.uuid))
    line("type", message_helper.display_name(report.incident_type))
    line("status", lm.get(lang, "general", "open" if report.opened else "closed"))
    line("automated", lm.get(lang, "general", "yes" if report.automated else "no"))
    if report.message is not None and report.message.strip():
        line("message", html_escape.escape(report.message))

    html.append("\n")
    line("entity", html_escape.escape(report.reporting_entity))
    line("submitted_by", html_escape.escape(report.submitting_operator))
    assigned = (
        f"<code>{html_escape.escape(report.assigned_operator)}</code>"
        if report.assigned_operator is not None and report.assigned_operator.strip()
        else lm.get(lang, "report", "unassigned")
    )
    line("assigned_to", assigned)
    line("created", message_helper.format_timestamp(report.created))
    line("updated", message_helper.format_updated(lm, lang, report.updated or 0))

    html.append("\n")
    html.append(lm.get(lang, "report", "evidence_header"))
    if not evidence:
        html.append(lm.get(lang, "report", "evidence_none"))
    html.extend(
        f"\N{BULLET} <code>{html_escape.escape(record.uuid)}</code>\n" for record in evidence
    )

    return "".join(html)
