"""Content-building helpers for moderation notifications.

Renders user mentions, chat references and bounded, HTML-escaped detail text. Delivery is handled
by NotificationSender.
"""

from pyrogram.types import User

from spb.classes.language_manager import LanguageManager
from spb.classes.managers.user_manager import UserManager
from spb.objects.language import Language
from spb.utilities import html_escape

MAX_DETAIL_LENGTH = 1_500
"""Maximum number of characters of untrusted detail text embedded in a notification."""

_MAX_NAME_LENGTH = 256


def user_mention(lm: LanguageManager, lang: Language, user: User | None) -> str:
    """Renders an inline Telegram mention for a user.

    When the user has a username the visible text is @username, which Telegram renders as a
    clickable link to the user's profile. When no username is set the display name is wrapped in a
    tg://user?id= deep link so tapping it still navigates to the profile.

    Returns:
        The HTML-safe inline mention, or "Unknown user" when there is no user.
    """
    if user is None:
        return lm.get(lang, "chat_notification", "unknown_user")
    return _mention(lm, lang, user.id, user.username, user.first_name, user.last_name)


def user_id_mention(lm: LanguageManager, lang: Language, user_id: int, users: UserManager) -> str:
    """Renders an inline Telegram mention for a user id, resolving the user's identity from the
    database like user_mention() and falling back to the bare user id when it is not known."""
    identity = users.get_user(user_id)
    if identity is None:
        return _mention(lm, lang, user_id, None, None, None)
    return _mention(lm, lang, user_id, identity.username, identity.first_name, identity.last_name)


def chat_reference(lm: LanguageManager, lang: Language, chat_name: str | None, chat_id: int) -> str:
    """Renders a protected-chat label without relying on a public username.

    Args:
        chat_name: The cached chat title, or None.
    """
    name = (
        chat_name
        if chat_name is not None and chat_name.strip()
        else lm.get(lang, "chat_notification", "chat_id_fallback", chat_id)
    )
    return f"{escape_and_truncate(lm, lang, name, _MAX_NAME_LENGTH)} (<code>{chat_id}</code>)"


def escape_and_truncate(
    lm: LanguageManager, lang: Language, value: str | None, maximum_length: int
) -> str:
    """Escapes untrusted notification detail and bounds its rendered size.

    Args:
        maximum_length: The maximum number of characters kept before escaping.
    """
    if value is None or not value.strip():
        return lm.get(lang, "chat_notification", "no_text")
    suffix = "\N{HORIZONTAL ELLIPSIS}" if len(value) > maximum_length else ""
    return html_escape.escape(value[:maximum_length]) + suffix


def _mention(
    lm: LanguageManager,
    lang: Language,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> str:
    """Renders @username when one is set, otherwise the display name wrapped in a tg://user?id=
    deep link, falling back to the user id when no name is known."""
    if username is not None and username.strip():
        return "@" + escape_and_truncate(lm, lang, username, _MAX_NAME_LENGTH)

    name = " ".join(part for part in (first_name, last_name) if part is not None and part.strip())
    if not name:
        name = lm.get(lang, "chat_notification", "user_id_fallback", user_id)

    return (
        f'<a href="tg://user?id={user_id}">'
        f"{escape_and_truncate(lm, lang, name, _MAX_NAME_LENGTH)}</a> (<code>{user_id}</code>)"
    )
