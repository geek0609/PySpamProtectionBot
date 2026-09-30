"""A report being filed."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Self

from pyfederation.enums import IncidentType

from spb.enums import ReportPage
from spb.objects.report_attachment import ReportAttachment


@dataclass(frozen=True, slots=True)
class ReportContext:
    """A report being filed against a message, from the report dialog or directly.

    Attributes:
        hash: The opaque id carried in the dialog's callback data, empty for a report that was
            never a dialog.
        reporter_id: The user filing the report.
        chat_id: The chat the reported message was sent in.
        target_message_id: The reported message.
        target_author_id: The author of the reported message, or 0 when unknown.
        target_text: The text or caption of the reported message, or None.
        attachments: The files attached to the reported message.
        target_metadata: The reported message, flattened into metadata.
        prompt_message_id: The dialog's prompt message, which a comment replies to, or None.
        ephemeral: Whether the dialog is an ephemeral message only the reporter sees.
        message_thread_id: The forum topic the reported message was sent in, or None.
        page: The dialog page shown, or None for a report that was never a dialog.
        incident_type: The chosen incident type, or None until one is chosen.
        reporter_is_admin: Whether the reporter moderates the chat.
    """

    hash: str
    reporter_id: int
    chat_id: int
    target_message_id: int
    target_author_id: int
    target_text: str | None
    attachments: Sequence[ReportAttachment]
    target_metadata: Mapping[str, object] = field(default_factory=dict)
    prompt_message_id: int | None = None
    ephemeral: bool = False
    message_thread_id: int | None = None
    page: ReportPage | None = None
    incident_type: IncidentType | None = None
    reporter_is_admin: bool = False

    def touch(self) -> Self:
        """Returns the session as it is; storing it again is what refreshes its expiry."""
        return self

    def with_page(self, page: ReportPage) -> Self:
        return replace(self, page=page)

    def with_incident_type(self, incident_type: IncidentType) -> Self:
        return replace(self, incident_type=incident_type)

    def with_prompt_message_id(self, prompt_message_id: int | None) -> Self:
        return replace(self, prompt_message_id=prompt_message_id)
