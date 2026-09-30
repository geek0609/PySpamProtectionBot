"""Escapes text for Telegram HTML messages, and turns such HTML back into plain text."""

import re

_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def escape(value: str | None) -> str:
    """Escapes &, < and > so the value can be embedded in a Telegram HTML message.

    Returns:
        The escaped string, or an empty string when the value is None.
    """
    if value is None:
        return ""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_plain_text(html: str | None) -> str:
    """Converts Telegram HTML to the plain text it displays.

    Tags are removed and the entities Telegram supports are decoded. Used for surfaces Telegram
    always shows as plain text, such as callback query alerts and toasts.

    Returns:
        The plain text, or an empty string when the HTML is None.
    """
    if html is None:
        return ""
    return (
        _TAG.sub("", html)
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&amp;", "&")
    )
