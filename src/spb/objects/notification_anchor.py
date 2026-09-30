"""A notification destination prepared ahead of a moderation action."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NotificationAnchor:
    """One notification destination prepared ahead of a moderation action.

    It holds the copy of the offending message forwarded there, so the notification sent afterwards
    can reply to it. The message is forwarded before the action because Telegram cannot forward a
    message once it has been deleted.

    Attributes:
        destination: The chat or user the notification goes to.
        message_thread_id: The forum topic to post in, or None.
        reply_to_message_id: The forwarded copy to reply to, or None when it could not be forwarded.
    """

    destination: int
    message_thread_id: int | None
    reply_to_message_id: int | None
