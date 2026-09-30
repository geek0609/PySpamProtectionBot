"""A delivered copy of a report notification carrying moderation buttons."""

from dataclasses import dataclass

from pyrogram.types import InlineKeyboardMarkup


@dataclass(frozen=True, slots=True)
class NotificationTarget:
    """One delivered copy of a report notification that carries moderation buttons.

    It is recorded so the buttons can be removed from every copy once a moderator acts.

    Attributes:
        chat_id: The chat the notification was delivered to, usually a moderator's private chat.
        message_id: The notification message carrying the buttons.
        retained_markup: The keyboard left on the notification once its moderation buttons are
            removed, such as its link to the Federation Web Application, or None to leave no
            keyboard.
    """

    chat_id: int
    message_id: int
    retained_markup: InlineKeyboardMarkup | None = None
