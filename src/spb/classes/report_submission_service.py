"""Creates reports on the Federation server, whoever asked for one.

Three different features end in the same place: a member reporting a message with /report, a
moderator marking a scan result as wrong, and an operator blacklisting an entity against a freshly
created supporting report. They differ only in what they do afterwards: whether a summary goes back
to the chat, whether moderators are notified, which evidence tag is attached, and whose credentials
the submission is made under. Everything before that point is identical, so it lives here rather
than being reached into across three handler packages.

Submission never raises on failure. Federation being unreachable must not lose the user's report
silently either, so the caller is told through the returned UUID being None and the user is told
through a message in their own language.
"""

import logging
import tempfile
import time
from pathlib import Path

from pyfederation.enums import IncidentType
from pyfederation.records import ContentInput, ReportSubmission
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from spb.classes.federation_web_links import FederationWebLinks, Record
from spb.classes.language_manager import LanguageManager
from spb.classes.notifications import notification_formatter, notification_sender
from spb.enums import ReportOrigin
from spb.exceptions import FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.context.report_context import ReportContext
from spb.objects.language import Language
from spb.objects.report_attachment import ReportAttachment
from spb.utilities import entity_publisher, flat_metadata, html_escape, message_helper

log = logging.getLogger(__name__)

TELEGRAM_ENTITY_SUFFIX = "@telegram.org"
"""Federation addresses every Telegram entity under this host."""

DISMISS_CALLBACK = "report:dismiss"
"""Callback data of the Dismiss button on a submission summary.

It belongs to the report handler's namespace because that is what answers the press; it is
declared here because this is what puts the button on the message."""


async def submit(
    context: HandlerContext, session: ReportContext, report_message: str | None
) -> None:
    """Submits the report described by the session and sends a basic HTML summary, by editing the
    report prompt when there is one, or by sending a new response in the originating chat.

    An administrator's own /report still reaches Federation and still summarises back to them, but
    does not also notify the chat's moderators: the reporter already is one, so there is nobody
    left to alert.

    Args:
        report_message: The reporter's comment, or None.

    Raises:
        RPCError: If the summary or error reply cannot be sent.
    """
    origin = ReportOrigin.ADMIN if session.reporter_is_admin else ReportOrigin.MEMBER
    await _submit(context, session, origin, report_message, None)


async def submit_operator_reference(
    context: HandlerContext, access_token: str, session: ReportContext
) -> str | None:
    """Submits a report on behalf of an authenticated operator purely to obtain a supporting report
    reference, typically to back a /blacklist record.

    The report is created exactly like a user-initiated report (evidence, metadata, and attachments
    are captured and uploaded) but neither a moderator notification nor a user summary is emitted:
    the report exists solely as a reference for the blacklist.

    Returns:
        The submitted report UUID, or None when Federation rejected the report.
    """
    return await _submit(context, session, ReportOrigin.OPERATOR_REFERENCE, None, access_token)


def dismiss_markup(lm: LanguageManager, lang: Language) -> InlineKeyboardMarkup:
    """Builds the keyboard holding the Dismiss button of a submission summary."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(lm.get(lang, "general", "dismiss"), callback_data=DISMISS_CALLBACK)]]
    )


async def _submit(
    context: HandlerContext,
    session: ReportContext,
    origin: ReportOrigin,
    report_message: str | None,
    access_token: str | None,
) -> str | None:
    """Creates the report for the given session.

    Args:
        origin: Who asked for the report, which decides its tag and who hears about it.
        access_token: The operator's access token, or None to submit as the bot.

    Returns:
        The submitted report UUID, or None when Federation rejected the report.
    """
    incident_type = session.incident_type
    assert incident_type is not None, "a report is submitted once its incident type is chosen"

    lm = context.languages
    lang = lm.default_language
    if not context.federation.is_available:
        if origin.is_summarised:
            await _send_error(
                context, lang, session, lm.get(lang, "report_submit", "not_configured")
            )
        return None

    # Submitting as the bot itself is refused unconditionally without client permissions, no matter
    # what the server allows anonymously, so there is nothing to gain from attempting it. An
    # operator-backed submission is unaffected: it lives or dies on that operator's own
    # permissions, not the bot's.
    if access_token is None and not context.federation.is_authenticated:
        if origin.is_summarised:
            await _send_error(
                context, lang, session, lm.get(lang, "report_submit", "not_authorized")
            )
        return None

    if incident_type == IncidentType.ILLEGAL_CONTENT and not await is_illegal_content_allowed(
        context
    ):
        if origin.is_summarised:
            await _send_error(
                context,
                lang,
                session,
                lm.get(lang, "report_submit", "illegal_content_not_allowed"),
            )
        return None

    update_message = context.update.message if context.update is not None else None
    if update_message is not None:
        preferences = context.managers.language_preferences
        if message_helper.is_group(update_message.chat):
            lang = preferences.get_chat_language(update_message.chat.id)
        elif update_message.from_user is not None:
            lang = preferences.get_user_language(update_message.from_user.id)

    reporting_entity = f"{session.target_author_id}{TELEGRAM_ENTITY_SUFFIX}"
    configuration = context.managers.chat_configurations.resolve(session.chat_id)
    evidence = _build_evidence(session, configuration.privacy_mode, origin.evidence_tag)

    log.debug(
        "Submitting report for entity %s from reporter %s in chat %s: incidentType=%s, "
        "targetMessageId=%s, textLength=%d",
        reporting_entity,
        session.reporter_id,
        session.chat_id,
        incident_type.name,
        session.target_message_id,
        len(evidence.text_content or ""),
    )

    try:
        # A report cannot be submitted against an entity unknown to the server. Whoever started the
        # report has usually published the target with its properties already; this bare publish
        # only guarantees it exists, and never erases what that earlier publish sent.
        await entity_publisher.publish(context, session.chat_id, session.target_author_id, None)

        submission = await context.federation.submit_report(
            access_token, reporting_entity, evidence, incident_type, report_message
        )
    except FederationException as e:
        log.warning(
            "Failed to submit report for message %s in chat %s: %s",
            session.target_message_id,
            session.chat_id,
            e,
        )
        if origin.is_summarised:
            await _send_error(
                context, lang, session, lm.get(lang, "report_submit", "submit_failed")
            )
        return None

    report_uuid = submission.report.uuid
    await _upload_attachments(
        context, access_token, submission, session, configuration.privacy_mode
    )
    log.debug("Report submitted successfully: uuid=%s", report_uuid)

    if origin.notifies_moderators and configuration.reporting_notifications_enabled:
        html = _notification_html(context, lang, session, report_uuid, report_message)
        targets = await notification_sender.notify_report_submitted(
            context,
            session.chat_id,
            html,
            session.target_message_id,
            session.target_author_id,
            True,
            context.web_links.button(lm, lang, Record.REPORT, report_uuid),
        )
        if not context.sessions.report_actions.track(
            session.chat_id, session.target_message_id, targets
        ):
            await notification_sender.remove_action_buttons(context, targets)

    if origin.is_summarised:
        await _send_summary(context, lang, session, report_uuid, report_message, origin)
    return report_uuid


async def is_illegal_content_allowed(context: HandlerContext) -> bool:
    """Checks whether the Federation server accepts reports with the ILLEGAL_CONTENT incident type.

    A server that declines illegal content rejects such reports with HTTP 403. When the server
    information cannot be read, the report is left for the server to accept or reject.

    Returns:
        False only when the server publishes that it does not accept illegal content.
    """
    information = await message_helper.server_information(context)
    return information is None or information.illegal_content_allowed


def _notification_html(
    context: HandlerContext,
    lang: Language,
    session: ReportContext,
    report_uuid: str,
    report_message: str | None,
) -> str:
    """Builds the notification the chat's moderators receive about a submitted report."""
    lm = context.languages
    users = context.managers.users
    chat = context.chat_info.get_if_present(session.chat_id)
    chat_name = chat.name if chat is not None else None

    reference = notification_formatter.chat_reference(lm, lang, chat_name, session.chat_id)
    reported_user = notification_formatter.user_id_mention(
        lm, lang, session.target_author_id, users
    )
    reporter = notification_formatter.user_id_mention(lm, lang, session.reporter_id, users)
    incident_type = html_escape.escape(message_helper.display_name(session.incident_type))
    if report_message is not None and report_message.strip():
        comment = lm.get(
            lang,
            "report_submit",
            "comment",
            notification_formatter.escape_and_truncate(lm, lang, report_message, 800),
        )
    else:
        comment = lm.get(lang, "report_submit", "comment_none")

    return (
        lm.get(lang, "report_submit", "title")
        + lm.get(lang, "report_submit", "chat", reference)
        + "\n"
        + lm.get(lang, "report_submit", "reported_user", reported_user)
        + "\n"
        + lm.get(lang, "report_submit", "reporter", reporter)
        + "\n"
        + lm.get(lang, "report_submit", "report_type", incident_type)
        + "\n"
        + lm.get(lang, "report_submit", "report_id", html_escape.escape(report_uuid))
        + "\n"
        + comment
        + "\n"
    )


def _build_evidence(
    session: ReportContext, privacy_mode: bool, evidence_tag: str | None
) -> ContentInput:
    """Builds the evidence of a report from the reported message.

    Args:
        privacy_mode: Whether the chat's privacy mode leaves the metadata out.
        evidence_tag: The evidence tag, or None to choose one by who is reporting.
    """
    text_content = (
        session.target_text
        if session.target_text is not None and session.target_text.strip()
        else "No text content"
    )
    if privacy_mode and evidence_tag is None:
        return ContentInput(text_content=text_content)

    metadata = None
    if not privacy_mode:
        fields = {
            "chat_id": session.chat_id,
            "message_id": session.target_message_id,
            "author_id": session.target_author_id,
            "reporter_id": session.reporter_id,
            "reported_at": int(time.time()),
        }
        # The reported message itself, every property of it, after the report's own fields.
        metadata = flat_metadata.with_fields(fields, session.target_metadata)

    if evidence_tag is None:
        origin = (
            ReportOrigin.OPERATOR_REFERENCE if session.reporter_is_admin else ReportOrigin.MEMBER
        )
        evidence_tag = origin.evidence_tag
    log.debug(
        "Built evidence for report: authorId=%s, targetMessageId=%s, textContent=%s",
        session.target_author_id,
        session.target_message_id,
        text_content,
    )
    return ContentInput(text_content=text_content, tag=evidence_tag, metadata=metadata)


async def _upload_attachments(
    context: HandlerContext,
    access_token: str | None,
    submission: ReportSubmission,
    session: ReportContext,
    privacy_mode: bool,
) -> None:
    """Uploads the attachments of the reported message to the report's evidence, unless privacy
    mode is on."""
    if privacy_mode or not session.attachments:
        return

    if not submission.evidence:
        log.warning(
            "Report %s returned no evidence record; attachments were not uploaded",
            submission.report.uuid,
        )
        return

    evidence_uuid = submission.evidence[0].uuid
    for attachment in session.attachments:
        await _upload_attachment(context, access_token, evidence_uuid, attachment)


async def _upload_attachment(
    context: HandlerContext,
    access_token: str | None,
    evidence_uuid: str,
    attachment: ReportAttachment,
) -> None:
    """Uploads one attachment to an evidence record, downloading it from Telegram first."""
    try:
        with tempfile.TemporaryDirectory(prefix="spb-report-") as directory:
            staged = Path(
                await context.telegram_client.download_media(
                    attachment.file_id, file_name=directory + "/"
                )
            )
            file_name = attachment.file_name or staged.name
            await context.federation.upload_attachment(
                access_token, evidence_uuid, staged, file_name
            )
        log.debug("Uploaded Telegram file %s to evidence %s", attachment.file_id, evidence_uuid)
    except Exception as e:
        log.warning(
            "Failed to upload Telegram file %s for evidence %s: %s",
            attachment.file_id,
            evidence_uuid,
            e,
        )


async def _send_summary(
    context: HandlerContext,
    lang: Language,
    session: ReportContext,
    report_uuid: str,
    report_message: str | None,
    origin: ReportOrigin,
) -> None:
    """Tells the reporter the report was submitted, with its id and what it was filed as."""
    lm = context.languages
    reported_user = notification_formatter.user_id_mention(
        lm, lang, session.target_author_id, context.managers.users
    )
    incident_type = message_helper.display_name(session.incident_type)

    html = [lm.get(lang, "report_submit", "summary_header")]
    html.append(
        lm.get(lang, "report_submit", "summary_report_id", html_escape.escape(report_uuid)) + "\n"
    )
    html.append(
        lm.get(lang, "report_submit", "summary_incident_type", html_escape.escape(incident_type))
        + "\n"
    )
    html.append(lm.get(lang, "report_submit", "summary_reported_user", reported_user) + "\n")
    html.append(
        lm.get(
            lang, "report_submit", "summary_evidence_tag", html_escape.escape(origin.evidence_tag)
        )
        + "\n"
    )
    if report_message is not None and report_message.strip():
        comment = html_escape.escape(report_message)
        html.append(lm.get(lang, "report_submit", "summary_comment", comment) + "\n")
    html.append(lm.get(lang, "report_submit", "summary_dismiss"))

    markup = FederationWebLinks.attach(
        dismiss_markup(lm, lang), context.web_links.button(lm, lang, Record.REPORT, report_uuid)
    )
    await _send_in_origin_chat(context, session, "".join(html), markup)


async def _send_error(
    context: HandlerContext, lang: Language, session: ReportContext, text: str
) -> None:
    """Tells the reporter the report could not be submitted, and why."""
    lm = context.languages
    html = (
        lm.get(lang, "report_submit", "error_header")
        + html_escape.escape(text)
        + "\n\n"
        + lm.get(lang, "report_submit", "error_dismiss")
    )
    await _send_in_origin_chat(context, session, html, dismiss_markup(lm, lang))


async def _send_in_origin_chat(
    context: HandlerContext,
    session: ReportContext,
    html: str,
    markup: InlineKeyboardMarkup | None,
) -> None:
    """Edits the report prompt to show the message, or sends it in the originating chat when the
    report had no prompt."""
    client = context.telegram_client
    if session.prompt_message_id is None:
        await client.send_message(
            session.chat_id,
            html,
            parse_mode=ParseMode.HTML,
            message_thread_id=session.message_thread_id,
            ephemeral_message_parameters=message_helper.ephemeral_to(
                session.reporter_id if session.ephemeral else None
            ),
            reply_markup=markup,
        )
        return

    if session.ephemeral:
        await client.edit_ephemeral_message_text(
            chat_id=session.chat_id,
            receiver_user_id=session.reporter_id,
            ephemeral_message_id=session.prompt_message_id,
            text=html,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
        return

    await client.edit_message_text(
        chat_id=session.chat_id,
        message_id=session.prompt_message_id,
        text=html,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )
