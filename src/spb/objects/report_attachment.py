"""A downloadable Telegram file attached to a reported message."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReportAttachment:
    """Downloadable Telegram file attached to a message being reported.

    The record holds only the Telegram file id, which is resolved to actual bytes lazily at
    report-submission time.

    Attributes:
        file_id: The Telegram file id the file is downloaded with.
        file_name: The name supplied to the Federation attachment upload, or None.
    """

    file_id: str
    file_name: str | None
