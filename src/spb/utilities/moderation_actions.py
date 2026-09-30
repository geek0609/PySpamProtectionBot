"""Telegram moderation actions: delete, restrict, ban, and join request decisions.

Every function is self-contained and returns whether it succeeded, with failures logged at the
appropriate level. This centralises the Telegram calls every moderation entry point needs.
"""

import logging
import time
from datetime import UTC, datetime

from pyrogram.errors import RPCError
from pyrogram.types import ChatPermissions

from spb.objects.context.handler_context import HandlerContext

log = logging.getLogger(__name__)

_ALL_DENIED = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)
"""Every send permission denied."""

_TEXT_ONLY = ChatPermissions(
    can_send_messages=True,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)
"""Only plain-text messages allowed."""


async def delete_message(context: HandlerContext, chat_id: int, message_id: int) -> bool:
    """Deletes a message from a chat.

    Returns:
        True if the message was deleted.
    """
    try:
        # Telegram reports a message that is already gone by deleting nothing.
        return await context.telegram_client.delete_messages(chat_id, message_id) > 0
    except RPCError as e:
        log.debug("Unable to delete message %s in chat %s: %s", message_id, chat_id, e)
        return False


async def restrict_user(
    context: HandlerContext, chat_id: int, user_id: int, until_epoch_seconds: int | None
) -> bool:
    """Restricts a user in a chat, revoking all send permissions.

    Args:
        until_epoch_seconds: The Unix time at which the restriction lifts (Telegram's until date,
            not a duration; see until_from_now()), or None for permanent.

    Returns:
        True if the restriction was applied.
    """
    try:
        await context.telegram_client.restrict_chat_member(
            chat_id, user_id, _ALL_DENIED, _until_date(until_epoch_seconds)
        )
        return True
    except RPCError as e:
        log.warning("Unable to restrict user %s in chat %s: %s", user_id, chat_id, e)
        return False


async def ban_user(
    context: HandlerContext, chat_id: int, user_id: int, until_epoch_seconds: int | None
) -> bool:
    """Bans a user from a chat.

    Args:
        until_epoch_seconds: The Unix time at which the ban lifts (Telegram's until date, not a
            duration; see until_from_now()), or None for permanent.

    Returns:
        True if the ban was applied.
    """
    try:
        await context.telegram_client.ban_chat_member(
            chat_id, user_id, _until_date(until_epoch_seconds)
        )
        return True
    except RPCError as e:
        log.warning("Unable to ban user %s in chat %s: %s", user_id, chat_id, e)
        return False


async def restrict_media_only(context: HandlerContext, chat_id: int, user_id: int) -> bool:
    """Restricts a user so that only plain-text messages are allowed (no media).

    Returns:
        True if the restriction was applied.
    """
    try:
        await context.telegram_client.restrict_chat_member(chat_id, user_id, _TEXT_ONLY)
        return True
    except RPCError as e:
        log.warning(
            "Unable to apply media-only restriction to user %s in chat %s: %s", user_id, chat_id, e
        )
        return False


async def approve_join_request(context: HandlerContext, chat_id: int, user_id: int) -> bool:
    """Approves a pending chat join request.

    Returns:
        True if the request was approved.
    """
    try:
        await context.telegram_client.approve_chat_join_request(chat_id, user_id)
        return True
    except RPCError as e:
        log.warning(
            "Unable to approve join request from user %s in chat %s: %s", user_id, chat_id, e
        )
        return False


async def decline_join_request(context: HandlerContext, chat_id: int, user_id: int) -> bool:
    """Declines a pending chat join request.

    Returns:
        True if the request was declined.
    """
    try:
        await context.telegram_client.decline_chat_join_request(chat_id, user_id)
        return True
    except RPCError as e:
        log.warning(
            "Unable to decline join request from user %s in chat %s: %s", user_id, chat_id, e
        )
        return False


def until_from_now(seconds: int) -> int:
    """Returns the Unix time a given number of seconds from now, for the until argument of
    restrict_user() and ban_user().

    Telegram's until date is an absolute time, and it treats one less than 30 seconds from now (or
    more than 366 days away) as "forever": passing a bare duration such as 3600 means 1970 and
    silently makes a one-hour mute permanent.

    Args:
        seconds: How long from now, at least 30 for Telegram to honour it.
    """
    return int(time.time()) + seconds


def temporary_restriction_until(suggested_lift_timestamp: int | None) -> int:
    """Computes the Unix time at which a temporary restriction should lift.

    Uses the Federation-suggested lift timestamp when it is sufficiently far in the future;
    otherwise defaults to 24 hours from now.
    """
    now = int(time.time())
    # Telegram's until date is a 32-bit field.
    if suggested_lift_timestamp is not None and now + 30 < suggested_lift_timestamp <= 2**31 - 1:
        return suggested_lift_timestamp
    return now + 86_400


def _until_date(epoch_seconds: int | None) -> datetime | None:
    return datetime.fromtimestamp(epoch_seconds, UTC) if epoch_seconds is not None else None
