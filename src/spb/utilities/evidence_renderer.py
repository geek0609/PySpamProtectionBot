"""Presents Federation evidence records in Telegram: the HTML summary of a record, and its file
attachments re-sent as documents.

Shared by the /evidence command and the evidence that follows a #REPORT_ASSIGNED notification, so
both show a record the same way.
"""

import json
import logging
import tempfile

from pyfederation.enums import ClassificationFlag
from pyfederation.records import EvidenceRecord
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.errors import RPCError
from pyrogram.types import ReplyParameters

from spb.classes.federation_service import FederationService
from spb.classes.language_manager import LanguageManager
from spb.exceptions import FederationException
from spb.objects.language import Language
from spb.utilities import html_escape, message_helper

log = logging.getLogger(__name__)


def info_html(lm: LanguageManager, lang: Language, evidence: EvidenceRecord) -> str:
    """Builds the HTML summary of an evidence record."""
    html = [lm.get(lang, "evidence", "header")]
    html.append(lm.get(lang, "evidence", "evidence_id", html_escape.escape(evidence.uuid)) + "\n")

    if evidence.classification_flag is not None:
        classification = _classification_display(lm, lang, evidence.classification_flag)
        html.append(lm.get(lang, "evidence", "classification", classification) + "\n")

    if evidence.tag is not None and evidence.tag.strip():
        html.append(lm.get(lang, "evidence", "tag", html_escape.escape(evidence.tag)) + "\n")

    confidential = lm.get(lang, "general", "yes" if evidence.confidential else "no")
    html.append(lm.get(lang, "evidence", "confidential", confidential) + "\n")

    if evidence.text_content is not None and evidence.text_content.strip():
        content = html_escape.escape(evidence.text_content)
        html.append(lm.get(lang, "evidence", "content", content) + "\n")

    if evidence.note is not None and evidence.note.strip():
        html.append(lm.get(lang, "evidence", "note", html_escape.escape(evidence.note)) + "\n")

    html.append("\n")
    html.append(lm.get(lang, "evidence", "entity", html_escape.escape(evidence.entity)) + "\n")
    submitted_by = html_escape.escape(evidence.operator)
    html.append(lm.get(lang, "evidence", "submitted_by", submitted_by) + "\n")

    if evidence.report is not None and evidence.report.strip():
        html.append(lm.get(lang, "evidence", "report", html_escape.escape(evidence.report)) + "\n")

    created = message_helper.format_timestamp(evidence.created)
    updated = message_helper.format_updated(lm, lang, evidence.updated)
    html.append(lm.get(lang, "evidence", "created", created) + "\n")
    html.append(lm.get(lang, "evidence", "updated", updated) + "\n")

    if evidence.metadata is not None:
        html.append("\n")
        html.append(lm.get(lang, "evidence", "metadata_header"))
        html.append(html_escape.escape(json.dumps(evidence.metadata, indent=2, ensure_ascii=False)))
        html.append("</pre>\n")

    return "".join(html)


async def send_attachments(
    client: Client,
    federation: FederationService,
    access_token: str | None,
    evidence: EvidenceRecord,
    chat_id: int,
    reply_to_message_id: int | None,
) -> None:
    """Downloads the file attachments of an evidence record and sends each one as a document.

    Failures are logged per attachment and never propagate.

    Args:
        access_token: The operator's access token, or None to read as the bot.
        reply_to_message_id: The message the documents reply to, or None.
    """
    try:
        attachments = await federation.evidence_attachments(access_token, evidence.uuid)
    except FederationException as e:
        log.debug("Failed to fetch attachments for evidence %s: %s", evidence.uuid, e)
        return

    if not attachments:
        return

    try:
        directory = tempfile.TemporaryDirectory(prefix="spb-evidence-")
    except OSError as e:
        log.warning("Failed to create temp directory for evidence attachments: %s", e)
        return

    reply_parameters = (
        ReplyParameters(message_id=reply_to_message_id) if reply_to_message_id is not None else None
    )
    with directory:
        for attachment in attachments:
            try:
                path = await federation.download_attachment(
                    access_token, attachment.uuid, directory.name
                )
                await client.send_document(
                    chat_id,
                    str(path),
                    caption=attachment.file_name or attachment.uuid,
                    parse_mode=ParseMode.DISABLED,
                    reply_parameters=reply_parameters,
                )
            except FederationException as e:
                log.debug("Failed to download attachment %s: %s", attachment.uuid, e)
            except RPCError as e:
                log.debug(
                    "Failed to send attachment %s in chat %s: %s", attachment.uuid, chat_id, e
                )


def _classification_display(lm: LanguageManager, lang: Language, flag: ClassificationFlag) -> str:
    match flag:
        case ClassificationFlag.MALICIOUS:
            return lm.get(lang, "evidence", "classification_malicious")
        case ClassificationFlag.SUSPICIOUS:
            return lm.get(lang, "evidence", "classification_suspicious")
        case ClassificationFlag.NORMAL:
            return lm.get(lang, "evidence", "classification_normal")
