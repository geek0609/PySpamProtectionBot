"""The open report dialogs."""

from collections.abc import Sequence
from datetime import timedelta

from pyfederation.enums import IncidentType
from pyrogram.types import Message

from spb.classes.sessions.abstract_session_manager import AbstractSessionManager, ReverseIndex
from spb.enums import ReportPage
from spb.objects.context.report_context import ReportContext
from spb.objects.report_attachment import ReportAttachment
from spb.objects.secretary_report_target import SecretaryReportTarget
from spb.utilities import flat_metadata, message_content

SESSION_EXPIRY = timedelta(minutes=5)


class ReportSessionManager(AbstractSessionManager[ReportContext]):
    """The open report dialogs.

    A dialog is found by its hash from a button press, or by its prompt message when the reporter
    replies to it with a comment. Only the reporter can take a dialog, and taking it closes it, so
    a report is submitted once.
    """

    def __init__(self) -> None:
        super().__init__(SESSION_EXPIRY)
        self._prompt_index: ReverseIndex[tuple[int, int]] = ReverseIndex(SESSION_EXPIRY)

    def create(
        self,
        command: Message,
        target: Message,
        message_thread_id: int | None,
        attachments: Sequence[ReportAttachment],
        reporter_is_admin: bool,
        ephemeral: bool,
    ) -> ReportContext:
        """Opens a dialog for a /report command replying to the target message.

        Args:
            command: The /report command.
            target: The reported message.
            message_thread_id: The forum topic the target was sent in, or None.
            attachments: The files attached to the target.
            reporter_is_admin: Whether the reporter moderates the chat.
            ephemeral: Whether the dialog is an ephemeral message only the reporter sees.
        """
        return self._store(
            ReportContext(
                self._generate_hash(),
                command.from_user.id,
                command.chat.id,
                target.id,
                target.from_user.id if target.from_user is not None else 0,
                message_content.text_or_caption(target),
                attachments,
                flat_metadata.of(target),
                ephemeral=ephemeral,
                message_thread_id=message_thread_id,
                page=ReportPage.INCIDENT,
                reporter_is_admin=reporter_is_admin,
            )
        )

    def create_from_forward(
        self,
        forward: Message,
        original_message_id: int,
        original_author_id: int,
        attachments: Sequence[ReportAttachment],
        reporter_is_admin: bool,
    ) -> ReportContext:
        """Opens a dialog for a message forwarded to the bot privately."""
        return self._store(
            ReportContext(
                self._generate_hash(),
                forward.from_user.id,
                forward.chat.id,
                original_message_id,
                original_author_id,
                message_content.text_or_caption(forward),
                attachments,
                flat_metadata.of(forward),
                page=ReportPage.INCIDENT,
                reporter_is_admin=reporter_is_admin,
            )
        )

    def create_from_secretary(self, target: SecretaryReportTarget) -> ReportContext:
        """Opens a dialog in the secretary owner's private chat to report a first-contact business
        message they were notified about.

        Args:
            target: The business message, as captured when the owner was notified.
        """
        return self._store(
            ReportContext(
                self._generate_hash(),
                target.owner_id,
                target.owner_id,
                target.message_id,
                target.contact_id,
                target.text,
                target.attachments,
                target.metadata,
                page=ReportPage.INCIDENT,
                reporter_is_admin=False,
            )
        )

    def take_owned(self, hash: str, reporter_id: int) -> ReportContext | None:
        """Closes and returns a dialog, when it belongs to the reporter.

        Returns:
            The session, or None when it expired, was already taken, or belongs to someone else.
        """
        session = self._cache.get_if_present(hash)
        if session is None or session.reporter_id != reporter_id:
            return None

        self._prompt_index.clear(_prompt_key(session))
        self._evict(session)
        return session

    def find_by_prompt_message_id(
        self, chat_id: int, prompt_message_id: int
    ) -> ReportContext | None:
        """Returns the dialog whose prompt message is the given one."""
        hash = self._prompt_index.resolve((chat_id, prompt_message_id))
        return self.find(hash) if hash is not None else None

    def take_owned_by_prompt_message_id(
        self, chat_id: int, prompt_message_id: int, reporter_id: int
    ) -> ReportContext | None:
        """Closes and returns the dialog whose prompt message is the given one, when it belongs to
        the reporter."""
        hash = self._prompt_index.resolve((chat_id, prompt_message_id))
        return self.take_owned(hash, reporter_id) if hash is not None else None

    def update_incident_type(self, hash: str, incident_type: IncidentType) -> ReportContext | None:
        """Records the chosen incident type and moves the dialog to its comment page.

        Returns:
            The updated session, or None when it has expired.
        """
        session = self.find(hash)
        if session is None:
            return None
        return self._store(session.with_incident_type(incident_type).with_page(ReportPage.COMMENT))

    def update_prompt_message_id(self, hash: str, prompt_message_id: int | None) -> None:
        """Records the dialog's prompt message, so a reply to it is found."""
        session = self.find(hash)
        if session is None:
            return

        self._prompt_index.clear(_prompt_key(session))
        session = self._store(session.with_prompt_message_id(prompt_message_id))
        if prompt_message_id is not None:
            self._prompt_index.store((session.chat_id, prompt_message_id), hash)

    def invalidate(self, hash: str) -> None:
        session = self._cache.get_if_present(hash)
        if session is not None:
            self._prompt_index.clear(_prompt_key(session))
        super().invalidate(hash)


def _prompt_key(session: ReportContext) -> tuple[int, int] | None:
    if session.prompt_message_id is None:
        return None
    return session.chat_id, session.prompt_message_id
