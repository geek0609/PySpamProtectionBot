"""Delivers a report notification to an operator when a report is assigned to them."""

import logging

from pyfederation.records import EvidenceRecord, ReportRecord
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.errors import RPCError
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters

from spb.classes.federation_service import FederationService
from spb.classes.federation_web_links import FederationWebLinks, Record
from spb.classes.language_manager import LanguageManager
from spb.classes.managers.manager_registry import ManagerRegistry
from spb.classes.notifications import operator_report_callback
from spb.exceptions import FederationException
from spb.objects.language import Language
from spb.utilities import entity_resolver, evidence_renderer, html_escape

log = logging.getLogger(__name__)

EVIDENCE_PAGE_SIZE = 100
MAX_MESSAGE_TEXT_LENGTH = 3_500


class ReportNotificationSender:
    """Delivers a report notification to an operator when a report is assigned to them.

    The message begins with #REPORT_ASSIGNED and contains the report metadata, the report message
    (if any), a list of evidence UUIDs, and the operator action keyboard. Each evidence record then
    follows as a reply to it, with the record's file attachments replying to the record.
    """

    def __init__(
        self,
        client: Client,
        federation: FederationService,
        managers: ManagerRegistry,
        languages: LanguageManager,
        web_links: FederationWebLinks,
    ) -> None:
        self._client = client
        self._federation = federation
        self._managers = managers
        self._languages = languages
        self._web_links = web_links

    async def send(self, telegram_user_id: int, report: ReportRecord) -> None:
        """Delivers the notification of one report to the operator's Telegram user.

        Raises:
            RPCError: If the notification itself could not be delivered.
        """
        lm = self._languages
        lang = self._managers.language_preferences.get_user_language(telegram_user_id)
        evidence = await self._load_evidence(report.uuid)
        evidence_ids = [record.uuid for record in evidence]

        notification = await self._client.send_message(
            telegram_user_id,
            await self._details_html(report, evidence_ids, lang),
            parse_mode=ParseMode.HTML,
            reply_markup=FederationWebLinks.attach(
                _action_markup(lm, lang, report.uuid),
                self._web_links.button(lm, lang, Record.REPORT, report.uuid),
            ),
        )

        await self._send_evidence(telegram_user_id, notification, evidence, lang)

    async def send_credential_revoked(self, telegram_user_id: int) -> None:
        """Tells an operator that the Federation server rejected their stored credential and that
        it has been removed."""
        lang = self._managers.language_preferences.get_user_language(telegram_user_id)
        await self._client.send_message(
            telegram_user_id,
            self._languages.get(lang, "authentication", "credential_revoked"),
            parse_mode=ParseMode.HTML,
        )

    async def _send_evidence(
        self,
        telegram_user_id: int,
        notification: Message,
        evidence: list[EvidenceRecord],
        lang: Language,
    ) -> None:
        """Sends each evidence record of the report as a reply to its notification, followed by the
        record's file attachments as replies to the record, so the operator sees the report and
        its evidence together.

        The notification has already been delivered at this point, so a failure is logged and
        never propagates: the caller would otherwise treat the whole notification as undelivered.
        """
        if not evidence:
            return

        # Confidential evidence and its files are visible only to the operator they belong to, so
        # attachments are read with the operator's own credential when one is stored.
        operator = self._managers.operators.get_operator(telegram_user_id)
        access_token = operator.access_token if operator is not None else None
        for record in evidence:
            reply_to_id = notification.id
            try:
                info = await self._client.send_message(
                    telegram_user_id,
                    evidence_renderer.info_html(self._languages, lang, record),
                    parse_mode=ParseMode.HTML,
                    reply_markup=FederationWebLinks.attach(
                        None,
                        self._web_links.button(self._languages, lang, Record.EVIDENCE, record.uuid),
                    ),
                    reply_parameters=ReplyParameters(message_id=notification.id),
                )
                reply_to_id = info.id
            except RPCError as e:
                log.warning(
                    "Unable to send evidence %s to Telegram user %s: %s",
                    record.uuid,
                    telegram_user_id,
                    e,
                )
            await evidence_renderer.send_attachments(
                self._client, self._federation, access_token, record, telegram_user_id, reply_to_id
            )

    async def _details_html(
        self, report: ReportRecord, evidence_ids: list[str], lang: Language
    ) -> str:
        lm = self._languages
        users = self._managers.users
        reported_entity = await entity_resolver.resolve(
            self._federation, users, report.reporting_entity
        )
        submitted_by = await entity_resolver.resolve(
            self._federation, users, report.submitting_operator
        )

        html = [lm.get(lang, "report_notification", "header")]
        html.append(
            lm.get(lang, "report_notification", "report_id", html_escape.escape(report.uuid)) + "\n"
        )
        incident_type = html_escape.escape(report.incident_type.name.replace("_", " "))
        html.append(lm.get(lang, "report_notification", "incident_type", incident_type) + "\n")
        html.append(lm.get(lang, "report_notification", "reported_entity", reported_entity) + "\n")
        html.append(lm.get(lang, "report_notification", "submitted_by", submitted_by) + "\n")
        html.append(lm.get(lang, "report_notification", "created", report.created) + "\n")

        message = (
            html_escape.escape(_truncate(report.message))
            if report.message is not None and report.message.strip()
            else lm.get(lang, "report_notification", "message_missing")
        )
        html.append(lm.get(lang, "report_notification", "message", message) + "\n")

        if evidence_ids:
            html.append(lm.get(lang, "report_notification", "evidence"))
            for evidence_id in evidence_ids:
                entry = html_escape.escape(evidence_id)
                html.append(lm.get(lang, "report_notification", "evidence_entry", entry) + "\n")

        html.append(lm.get(lang, "report_notification", "footer"))
        return "".join(html)

    async def _load_evidence(self, report_uuid: str) -> list[EvidenceRecord]:
        """Loads the evidence records of a report, or none when they cannot be read."""
        try:
            return await self._federation.report_evidence(report_uuid, EVIDENCE_PAGE_SIZE)
        except FederationException as e:
            log.debug("Failed to load evidence for report %s: %s", report_uuid, e)
            return []


def _action_markup(lm: LanguageManager, lang: Language, report_uuid: str) -> InlineKeyboardMarkup:
    """Builds the keyboard an operator closes the report with, with or without classifying it."""

    def button(key: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            lm.get(lang, "report_notification", key),
            callback_data=operator_report_callback.data(report_uuid, action),
        )

    return InlineKeyboardMarkup(
        [
            [
                button("close", operator_report_callback.CLOSE),
                button("close_normal", "NORMAL"),
            ],
            [
                button("close_suspicious", "SUSPICIOUS"),
                button("close_malicious", "MALICIOUS"),
            ],
        ]
    )


def _truncate(value: str) -> str:
    """Bounds the report message so the notification stays within Telegram's message length."""
    if len(value) <= MAX_MESSAGE_TEXT_LENGTH:
        return value
    return value[: MAX_MESSAGE_TEXT_LENGTH - 1] + "\N{HORIZONTAL ELLIPSIS}"
