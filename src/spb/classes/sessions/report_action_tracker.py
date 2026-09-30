"""The moderation buttons of report notifications, per reported message."""

from collections.abc import Sequence
from datetime import timedelta

from spb.classes.cache import Cache
from spb.objects.notification_target import NotificationTarget
from spb.objects.report_action_state import ReportActionState

CACHE_MAX_SIZE = 10_000
EXPIRY = timedelta(days=7)


class ReportActionTracker:
    """Remembers, per reported message, which report notifications carry moderation buttons and
    whether a moderator has already acted on them.

    This state is an optimisation, not a requirement: the buttons carry everything the action
    needs (see ReportActionCallback), so they keep working after the state expires or the bot
    restarts. What the state adds is that the first press wins, so the message is not deleted or
    its author banned twice, and that the buttons are removed from every moderator's copy of the
    notification, not only the one pressed.
    """

    def __init__(self) -> None:
        self._states: Cache[str, ReportActionState] = Cache(CACHE_MAX_SIZE, EXPIRY)

    def track(self, chat_id: int, message_id: int, targets: Sequence[NotificationTarget]) -> bool:
        """Records the notifications delivered for a report of a message, alongside any delivered
        for earlier reports of it, since an action on either removes the message they are all
        about.

        A moderator may press a button before every copy has been delivered and recorded. The
        copies are then still recorded, never replacing the claim, and False tells the caller to
        remove their buttons itself, since the moderator who acted may have missed them.

        Returns:
            True when the action is still open, False when a moderator already acted.
        """
        state = self._state(chat_id, message_id)
        state.add_targets(targets)
        return not state.is_consumed

    def claim(self, chat_id: int, message_id: int) -> ReportActionState | None:
        """Claims the moderation action on a reported message for the caller, exactly once.

        A message that is not tracked, because its state expired or the bot restarted since the
        notification was sent, is claimable too; the returned state then has no targets.

        Returns:
            The claimed state, or None when another moderator already acted.
        """
        state = self._state(chat_id, message_id)
        return state if state.claim() else None

    def _state(self, chat_id: int, message_id: int) -> ReportActionState:
        key = f"{chat_id}:{message_id}"
        state = self._states.get_if_present(key)
        if state is None:
            state = ReportActionState()
            self._states.put(key, state)
        return state
