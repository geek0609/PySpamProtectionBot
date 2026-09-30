"""A first-contact business message the secretary owner may still report."""

from dataclasses import dataclass
from typing import Any

from spb.objects.report_attachment import ReportAttachment


@dataclass(frozen=True, slots=True)
class SecretaryReportTarget:
    """A first-contact business message the secretary owner was notified about and may still report.

    Telegram offers no way to fetch a business message again later, so everything a report needs is
    captured when the notification is sent.

    Attributes:
        owner_id: The secretary owner, the only user allowed to report it.
        contact_id: The Telegram user id of whoever sent the message.
        message_id: The business message id.
        text: The text or caption of the message, or None.
        attachments: Downloadable files attached to the message.
        metadata: Every property of the message, flattened as Federation metadata.
    """

    owner_id: int
    contact_id: int
    message_id: int
    text: str | None
    attachments: list[ReportAttachment]
    metadata: dict[str, Any]
