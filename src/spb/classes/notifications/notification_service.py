"""Notifies operators of the reports newly assigned to them."""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Self

from pyfederation.records import ReportRecord
from pyrogram import Client

from spb.classes.federation_service import FederationService
from spb.classes.federation_web_links import FederationWebLinks
from spb.classes.interfaces import NotificationSink, ReportSource
from spb.classes.language_manager import LanguageManager
from spb.classes.managers.manager_registry import ManagerRegistry
from spb.classes.notifications.report_notification_sender import ReportNotificationSender
from spb.exceptions import DatabaseException, FederationException
from spb.objects.database.operator_identity import OperatorIdentity

log = logging.getLogger(__name__)

REPORT_PAGE_SIZE = 100

SHUTDOWN_TIMEOUT_SECONDS = 10
"""How long the shutdown waits for the polling task to finish its current pass."""


@dataclass(slots=True)
class _OperatorState:
    """The credential an operator was last polled with, and the reports already seen for them."""

    identity: OperatorIdentity
    seen_report_ids: set[str]


class NotificationService:
    """Polls each authenticated operator's assigned open reports and sends a Telegram notification
    for reports first observed after that operator's baseline poll.

    All report tracking is process-local. The first successful poll for an operator records the
    reports currently assigned to that operator without sending notifications. Every later
    successful poll notifies only for report UUIDs absent from that in-memory baseline. Adding,
    removing, or changing an operator credential resets that operator's baseline, which prevents an
    operator from being flooded with pre-existing reports after authentication changes.

    The service owns one task. It remains idle while the operators table is empty so operators
    authenticated after startup are picked up on the next interval. Federation requests use a
    short-lived client authenticated with the individual operator's stored token; the process-wide
    Federation client is never mutated. When the server rejects an operator's token outright
    (unknown, or the operator is disabled), the stored credential is removed and the operator is
    told, rather than retrying a request that cannot succeed on every interval.
    """

    def __init__(
        self,
        managers: ManagerRegistry,
        interval_seconds: float,
        report_source: ReportSource,
        notification_sink: NotificationSink,
    ) -> None:
        """
        Args:
            managers: The operator credential store.
            interval_seconds: How long to wait between completed passes.
            report_source: Reads the open reports assigned to an operator.
            notification_sink: Delivers notifications to operators.
        """
        self._managers = managers
        self._interval_seconds = interval_seconds
        self._report_source = report_source
        self._notification_sink = notification_sink
        self._operator_states: dict[int, _OperatorState] = {}
        self._closed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @classmethod
    def create(
        cls,
        managers: ManagerRegistry,
        client: Client,
        federation: FederationService,
        languages: LanguageManager,
        web_links: FederationWebLinks,
        interval_seconds: float,
    ) -> Self:
        """Creates a notification service backed by the Telegram client and Federation server."""

        async def opened_reports(identity: OperatorIdentity) -> list[ReportRecord]:
            return await federation.open_assigned_reports(identity.access_token, REPORT_PAGE_SIZE)

        sender = ReportNotificationSender(client, federation, managers, languages, web_links)
        return cls(managers, interval_seconds, opened_reports, sender)

    def start(self) -> None:
        """Starts the polling task. Calling this method more than once has no effect."""
        if self._closed.is_set():
            raise RuntimeError("Notification service is closed")
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="spb-notifications")

    async def poll(self) -> None:
        """Runs a single poll cycle."""
        try:
            operators = self._managers.operators.list_operators()
        except DatabaseException as e:
            log.warning("Unable to load authenticated operators for notification polling: %s", e)
            return

        for telegram_user_id in self._operator_states.keys() - operators.keys():
            del self._operator_states[telegram_user_id]
        for telegram_user_id, identity in operators.items():
            await self._poll_operator(telegram_user_id, identity)

    async def close(self) -> None:
        """Stops the service, waiting up to ten seconds for its current pass to finish."""
        if self._closed.is_set():
            return
        self._closed.set()
        if self._task is None:
            return

        try:
            await asyncio.wait_for(asyncio.shield(self._task), SHUTDOWN_TIMEOUT_SECONDS)
        except TimeoutError:
            log.warning("Notification service did not stop within 10 seconds")
            self._task.cancel()

    async def _poll_operator(self, telegram_user_id: int, identity: OperatorIdentity) -> None:
        """Polls the reports assigned to one operator and notifies them of the ones not seen
        before."""
        try:
            reports = await self._report_source(identity)
        except FederationException as e:
            if FederationService.is_credential_rejected(e):
                await self._revoke_operator(telegram_user_id, identity, e)
                return
            log.warning(
                "Unable to load opened reports for operator %s: %s", identity.operator_uuid, e
            )
            return
        except Exception as e:
            log.warning(
                "Unable to load opened reports for operator %s: %s", identity.operator_uuid, e
            )
            return

        state = self._operator_states.get(telegram_user_id)
        if state is None or state.identity != identity:
            seen = {report.uuid for report in reports}
            self._operator_states[telegram_user_id] = _OperatorState(identity, seen)
            log.debug(
                "Established notification baseline for Telegram user %s with %d open reports",
                telegram_user_id,
                len(reports),
            )
            return

        for report in reports:
            if report.uuid in state.seen_report_ids:
                continue
            state.seen_report_ids.add(report.uuid)

            try:
                await self._notification_sink.send(telegram_user_id, report)
            except Exception as e:
                log.warning(
                    "Unable to send notification for report %s to Telegram user %s: %s",
                    report.uuid,
                    telegram_user_id,
                    e,
                )

    async def _revoke_operator(
        self, telegram_user_id: int, identity: OperatorIdentity, cause: FederationException
    ) -> None:
        """Removes an operator credential the Federation server rejected and tells the operator, so
        the service stops sending the server requests it is certain to refuse.

        The operator list is a cached snapshot, so the operator may have re-authenticated with a
        new token since it was taken; the credential is only removed while it is still the one
        that was rejected.
        """
        self._operator_states.pop(telegram_user_id, None)
        operators = self._managers.operators
        try:
            if operators.get_operator(telegram_user_id) != identity:
                return
            operators.delete_operator(telegram_user_id)
        except DatabaseException as e:
            log.warning(
                "Unable to remove rejected credential for operator %s: %s",
                identity.operator_uuid,
                e,
            )
            return

        log.info(
            "Removed credential for operator %s (Telegram user %s) after the Federation server "
            "rejected it: %s",
            identity.operator_uuid,
            telegram_user_id,
            cause,
        )
        try:
            await self._notification_sink.send_credential_revoked(telegram_user_id)
        except Exception as e:
            log.warning(
                "Unable to notify Telegram user %s that their credential was removed: %s",
                telegram_user_id,
                e,
            )

    async def _run(self) -> None:
        while not self._closed.is_set():
            await self.poll()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._closed.wait(), self._interval_seconds)
