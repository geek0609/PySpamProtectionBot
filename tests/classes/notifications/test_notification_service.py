"""Tests for the first-poll baseline, duplicate suppression and credential revocation of operator
notifications."""

import pytest
from pyfederation.exceptions import FederationClientException
from pyfederation.records import ReportRecord

from spb.classes.notifications.notification_service import NotificationService
from spb.exceptions import FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.operator_identity import OperatorIdentity

pytestmark = pytest.mark.anyio

OPERATOR_USER = 42
IDENTITY = OperatorIdentity("operator-uuid", "token")


def report(uuid: str) -> ReportRecord:
    return ReportRecord.model_validate(
        {
            "uuid": uuid,
            "submitting_operator": "operator-uuid",
            "automated": False,
            "incident_type": "SPAM",
            "opened": True,
            "created": 1_700_000_000,
        }
    )


class Reports:
    """Stands in for the Federation server and for Telegram, recording what is delivered."""

    def __init__(self) -> None:
        self.open: list[ReportRecord] = []
        self.delivered: list[tuple[int, str]] = []
        self.revoked: list[int] = []
        self.failing: set[str] = set()
        self.rejection: FederationException | None = None

    async def source(self, identity: OperatorIdentity) -> list[ReportRecord]:
        if self.rejection is not None:
            raise self.rejection
        return self.open

    async def send(self, telegram_user_id: int, record: ReportRecord) -> None:
        if record.uuid in self.failing:
            raise RuntimeError("Telegram refused")
        self.delivered.append((telegram_user_id, record.uuid))

    async def send_credential_revoked(self, telegram_user_id: int) -> None:
        self.revoked.append(telegram_user_id)


def service(template: HandlerContext, reports: Reports) -> NotificationService:
    return NotificationService(template.managers, 60, reports.source, reports)


def rejection(status_code: int) -> FederationException:
    """A failed Federation call, caused by a response with the given status code."""
    rejected = FederationException("Failed to list open reports")
    rejected.__cause__ = FederationClientException("Unauthorized", status_code)
    return rejected


async def test_reports_open_at_the_first_poll_are_not_announced(template: HandlerContext) -> None:
    reports = Reports()
    polling = service(template, reports)
    template.managers.operators.save_operator(OPERATOR_USER, IDENTITY)
    reports.open = [report("old")]

    await polling.poll()
    reports.open = [report("old"), report("new")]
    await polling.poll()
    await polling.poll()

    assert reports.delivered == [(OPERATOR_USER, "new")]


async def test_a_changed_credential_starts_a_new_baseline(template: HandlerContext) -> None:
    reports = Reports()
    polling = service(template, reports)
    operators = template.managers.operators
    operators.save_operator(OPERATOR_USER, IDENTITY)
    await polling.poll()

    operators.save_operator(OPERATOR_USER, OperatorIdentity("operator-uuid", "rotated"))
    reports.open = [report("assigned-before")]
    await polling.poll()

    assert reports.delivered == []


async def test_a_failed_delivery_does_not_stop_the_others(template: HandlerContext) -> None:
    reports = Reports()
    polling = service(template, reports)
    template.managers.operators.save_operator(OPERATOR_USER, IDENTITY)
    await polling.poll()

    reports.open = [report("refused"), report("delivered")]
    reports.failing = {"refused"}
    await polling.poll()

    assert reports.delivered == [(OPERATOR_USER, "delivered")]


async def test_closing_stops_the_polling_task(template: HandlerContext) -> None:
    polling = service(template, Reports())

    polling.start()
    await polling.close()
    await polling.close()

    with pytest.raises(RuntimeError, match="closed"):
        polling.start()


@pytest.mark.parametrize("status_code", [401, 403])
async def test_a_rejected_credential_is_removed_and_the_operator_told(
    template: HandlerContext, status_code: int
) -> None:
    reports = Reports()
    polling = service(template, reports)
    template.managers.operators.save_operator(OPERATOR_USER, IDENTITY)
    reports.rejection = rejection(status_code)

    await polling.poll()

    assert template.managers.operators.get_operator(OPERATOR_USER) is None
    assert reports.revoked == [OPERATOR_USER]


async def test_an_unreachable_server_keeps_the_credential(template: HandlerContext) -> None:
    reports = Reports()
    polling = service(template, reports)
    template.managers.operators.save_operator(OPERATOR_USER, IDENTITY)
    reports.rejection = rejection(0)

    await polling.poll()

    assert template.managers.operators.get_operator(OPERATOR_USER) == IDENTITY
    assert reports.revoked == []
