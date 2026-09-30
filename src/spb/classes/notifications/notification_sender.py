"""Delivery layer for moderation notifications.

Resolves notification destinations from a chat's configuration and delivers HTML messages with
optional inline keyboards. Report notifications additionally forward the reported message to each
destination, reply to it with per-admin action buttons, and return the delivered notification
targets so the buttons can be removed once a moderator acts. Individual delivery failures are
isolated: a failure for one destination never prevents delivery to others.

Message content is built by notification_formatter.
"""

import logging
from collections.abc import Iterable

from pyrogram.enums import ParseMode
from pyrogram.errors import RPCError
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters

from spb.classes.federation_web_links import FederationWebLinks
from spb.classes.notifications.report_action_callback import Action, ReportActionCallback
from spb.objects.admin_info import AdminInfo
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.chat_configuration import ChatConfiguration
from spb.objects.notification_anchor import NotificationAnchor
from spb.objects.notification_target import NotificationTarget

log = logging.getLogger(__name__)


async def notify(
    context: HandlerContext,
    protected_chat_id: int,
    html: str | None,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Delivers an HTML moderation notification with an optional action keyboard to every
    destination resolved for the protected chat. Messages to the linked chat are posted in its
    configured topic, when one is set."""
    if html is None or not html.strip():
        return

    configuration = context.managers.chat_configurations.resolve(protected_chat_id)
    for destination in _resolve_destinations(context, configuration):
        thread_id = _thread_id(configuration, destination)
        await _send(context, destination, html, markup, thread_id, None)


async def forward_for_notification(
    context: HandlerContext, protected_chat_id: int, message_ids: Iterable[int]
) -> list[NotificationAnchor]:
    """Forwards the messages a moderation action is about to act on to every notification
    destination, before the action runs, so the notification sent afterwards with
    notify_anchored() can reply to the forwarded copy.

    This must happen first: Telegram cannot forward a deleted message, and the bot never re-sends
    a member's content itself. A destination the messages cannot be forwarded to (a chat that
    restricts saving content, for one) still receives the notification, but not as a reply.

    Args:
        message_ids: The messages to forward; several are forwarded together as one album.

    Returns:
        One anchor per destination, in delivery order.
    """
    configuration = context.managers.chat_configurations.resolve(protected_chat_id)
    ordered = sorted(message_ids)
    anchors = []
    for destination in _resolve_destinations(context, configuration):
        thread_id = _thread_id(configuration, destination)
        forwarded = await _forward(context, protected_chat_id, destination, ordered, thread_id)
        anchors.append(NotificationAnchor(destination, thread_id, forwarded))
    return anchors


async def notify_anchored(
    context: HandlerContext,
    anchors: Iterable[NotificationAnchor],
    html: str | None,
    markup: InlineKeyboardMarkup | None,
) -> None:
    """Sends a notification to each destination prepared by forward_for_notification(), as a
    reply to the message forwarded there."""
    if html is None or not html.strip():
        return
    for anchor in anchors:
        await _send(
            context,
            anchor.destination,
            html,
            markup,
            anchor.message_thread_id,
            anchor.reply_to_message_id,
        )


async def notify_report_submitted(
    context: HandlerContext,
    protected_chat_id: int,
    report_html: str,
    target_message_id: int,
    target_author_id: int,
    linked_chat_notification: bool,
    report_link: InlineKeyboardButton | None,
) -> list[NotificationTarget]:
    """Delivers a report notification by forwarding the reported message first, then sending the
    report details as a reply to the forwarded message with action buttons tailored to each
    admin's permissions. The linked chat receives the same notification without buttons.

    Args:
        report_html: The report notification HTML, without the content block.
        target_message_id: The reported message in the protected chat.
        target_author_id: The author of the reported message.
        linked_chat_notification: Whether the linked chat or channel also receives the
            notification.
        report_link: The button opening the report in the Federation Web Application, kept on
            every copy after its moderation buttons are removed, or None.

    Returns:
        The delivered notifications that carry moderation buttons, for removing them later.
    """
    link_markup = FederationWebLinks.attach(None, report_link)
    configuration = context.managers.chat_configurations.resolve(protected_chat_id)
    admins = context.chat_admins.get_if_present(protected_chat_id)
    targets: list[NotificationTarget] = []

    if configuration.moderator_notifications_enabled and admins is not None:
        bot_info = next((admin for admin in admins if admin.id == context.bot_user_id), None)
        moderators = {
            admin.id: admin
            for admin in admins
            if admin.id != context.bot_user_id and admin.is_moderator
        }
        for moderator, admin_info in moderators.items():
            markup = _build_action_buttons(
                admin_info, bot_info, protected_chat_id, target_message_id, target_author_id
            )
            await _deliver_report(
                context,
                protected_chat_id,
                moderator,
                report_html,
                markup,
                link_markup,
                target_message_id,
                targets,
            )

    if linked_chat_notification and configuration.channel_link_id is not None:
        await _deliver_report(
            context,
            protected_chat_id,
            configuration.channel_link_id,
            report_html,
            None,
            link_markup,
            target_message_id,
            targets,
        )
    return targets


async def remove_action_buttons(
    context: HandlerContext, targets: Iterable[NotificationTarget]
) -> None:
    """Removes the inline action keyboard from every notification of a report, leaving each copy
    with its retained keyboard, if any."""
    for target in targets:
        try:
            await context.telegram_client.edit_message_reply_markup(
                target.chat_id,
                target.message_id,
                reply_markup=target.retained_markup or InlineKeyboardMarkup([]),
            )
        except RPCError as e:
            log.debug(
                "Could not remove action buttons from notification %s in chat %s: %s",
                target.message_id,
                target.chat_id,
                e,
            )


def _resolve_destinations(context: HandlerContext, configuration: ChatConfiguration) -> list[int]:
    """Resolves the users and chats that receive moderation notifications for a protected chat.

    Includes the linked channel, if configured, and every cached moderator when moderator
    notifications are enabled. The bot itself is always excluded.
    """
    destinations: dict[int, None] = {}
    if configuration.channel_link_id is not None:
        destinations[configuration.channel_link_id] = None

    if configuration.moderator_notifications_enabled:
        admins = context.chat_admins.get_if_present(configuration.chat_id)
        for admin in admins or []:
            if admin.is_moderator:
                destinations[admin.id] = None
        destinations.pop(context.bot_user_id, None)

    return list(destinations)


def _thread_id(configuration: ChatConfiguration, destination: int) -> int | None:
    """The forum topic notifications are posted in at a destination: the configured topic of the
    linked chat, and none anywhere else."""
    if destination == configuration.channel_link_id:
        return configuration.channel_link_thread_id
    return None


async def _send(
    context: HandlerContext,
    destination: int,
    html: str,
    markup: InlineKeyboardMarkup | None,
    message_thread_id: int | None,
    reply_to_message_id: int | None,
) -> Message | None:
    """Sends an HTML message to a single destination, or returns None when it failed."""
    try:
        return await context.telegram_client.send_message(
            destination,
            html,
            parse_mode=ParseMode.HTML,
            message_thread_id=message_thread_id,
            reply_parameters=(
                ReplyParameters(message_id=reply_to_message_id)
                if reply_to_message_id is not None
                else None
            ),
            reply_markup=markup,
        )
    except RPCError as e:
        log.warning("Unable to send notification to %s: %s", destination, e)
        return None


async def _forward(
    context: HandlerContext,
    from_chat_id: int,
    to_chat_id: int,
    message_ids: list[int],
    message_thread_id: int | None,
) -> int | None:
    """Forwards messages to one destination, as an album when they form one.

    Returns:
        The id of the first forwarded message, or None when nothing was forwarded.
    """
    if not message_ids:
        return None
    try:
        forwarded = await context.telegram_client.forward_messages(
            to_chat_id, from_chat_id, message_ids, message_thread_id=message_thread_id
        )
    except RPCError as e:
        log.warning(
            "Unable to forward messages %s from chat %s to chat %s: %s",
            message_ids,
            from_chat_id,
            to_chat_id,
            e,
        )
        return None
    return forwarded[0].id if forwarded else None


async def _deliver_report(
    context: HandlerContext,
    protected_chat_id: int,
    destination: int,
    report_html: str,
    action_markup: InlineKeyboardMarkup | None,
    link_markup: InlineKeyboardMarkup | None,
    target_message_id: int,
    targets: list[NotificationTarget],
) -> None:
    """Forwards the reported message to one destination and replies to it with the report details,
    recording the delivered notification in targets when it carries moderation buttons. The link
    keyboard follows the moderation buttons and is what remains once they are removed."""
    reply_to = await _forward(context, protected_chat_id, destination, [target_message_id], None)

    markup = FederationWebLinks.keep_links(action_markup, link_markup)
    sent = await _send(context, destination, report_html, markup, None, reply_to)
    if sent is not None and action_markup is not None:
        targets.append(NotificationTarget(destination, sent.id, link_markup))


def _build_action_buttons(
    admin_info: AdminInfo,
    bot_info: AdminInfo | None,
    chat_id: int,
    message_id: int,
    author_id: int,
) -> InlineKeyboardMarkup | None:
    """Builds the moderation buttons both the receiving administrator and the bot may use, since
    the bot performs the action, or None when there are none."""
    buttons = [
        InlineKeyboardButton(
            text, callback_data=ReportActionCallback(chat_id, message_id, author_id, action).data
        )
        for text, action in (
            ("Delete Message", Action.DELETE),
            ("Delete + Mute 1h", Action.MUTE),
            ("Delete + Ban", Action.BAN),
        )
        if action.permits(admin_info) and action.permits(bot_info)
    ]
    return InlineKeyboardMarkup([buttons]) if buttons else None
