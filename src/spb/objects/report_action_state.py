"""The moderation buttons of one report notification."""

from collections.abc import Iterable

from spb.objects.notification_target import NotificationTarget


class ReportActionState:
    """The moderation buttons attached to one report notification, and whether they have been used.

    A report notification is delivered to every eligible moderator, so the same buttons exist in
    several chats at once. The first moderator to press one acts; the rest must find the action
    already taken rather than deleting the message twice or banning an already-banned member.
    claim() is what makes that race safe, and the target list is what lets the buttons be removed
    from every copy once one is used.

    Held by ReportActionTracker, keyed by the reported message, so it is written by whatever submits
    a report and read by whatever handles the button press.
    """

    def __init__(self) -> None:
        self._targets: list[NotificationTarget] = []
        self._consumed = False

    def add_targets(self, delivered: Iterable[NotificationTarget]) -> None:
        """Records further delivered copies of the notification.

        Copies can still be added after the action was claimed, since a moderator may press a
        button before every copy is delivered.
        """
        self._targets.extend(delivered)

    @property
    def targets(self) -> list[NotificationTarget]:
        """Every delivered copy of the notification recorded so far."""
        return list(self._targets)

    def claim(self) -> bool:
        """Claims the action for the caller, exactly once.

        Returns:
            True for the first caller, False for every later one.
        """
        claimed = not self._consumed
        self._consumed = True
        return claimed

    @property
    def is_consumed(self) -> bool:
        """Whether a moderator has already acted."""
        return self._consumed
