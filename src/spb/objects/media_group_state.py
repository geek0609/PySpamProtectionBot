"""The parts of one album seen so far."""

from spb.objects.report_attachment import ReportAttachment


class MediaGroupState:
    """Accumulates the messages and attachments of one media group for automated moderation.

    Instances are stored in the runtime cache keyed by chat and media group id. The restriction
    claim prevents an album from being deleted or restricted more than once.
    """

    def __init__(self, author_id: int) -> None:
        self.author_id = author_id
        self.message_ids: set[int] = set()
        self.attachments: dict[str, ReportAttachment] = {}
        self._restricted = False

    def add_message_id(self, message_id: int) -> None:
        self.message_ids.add(message_id)

    def add_attachment(self, attachment: ReportAttachment) -> None:
        self.attachments.setdefault(attachment.file_id, attachment)

    @property
    def is_restricted(self) -> bool:
        return self._restricted

    def restrict(self) -> bool:
        """Claims the media group for a one-time automated moderation action.

        Returns:
            True when this call performed the claim.
        """
        claimed = not self._restricted
        self._restricted = True
        return claimed
