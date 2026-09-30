"""Links a notification chat to a protected chat with /connect."""

import logging

from pyrogram.enums import ChatType
from pyrogram.errors import RPCError
from pyrogram.types import Message, MessageOriginChannel

from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.exceptions import DatabaseException
from spb.handlers.group import configuration_handler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.update import Update
from spb.utilities import message_helper

log = logging.getLogger(__name__)


@update_handler(UpdateType.COMMAND, UpdateType.CHANNEL_POST, commands=("connect",))
class ChannelConnectHandler(Handler):
    """Links a notification chat to a protected chat.

    The configuration menu of a protected chat shows a verification code. Posting /connect <code>
    in a channel, or in a group by one of its moderators, links that chat as where the protected
    chat's notifications go. The original command message is deleted when possible, and the
    configuration menu updates.
    """

    def accepts(self, context: HandlerContext) -> bool:
        # A channel post is not a command message, so the declared command filter cannot see it;
        # /connect is posted in the target channel itself.
        message = _incoming_message(context.current_update)
        if message is None or message.text is None:
            return False

        command = (message.text.split() or [""])[0]
        return command == "/connect" or command.startswith("/connect@")

    async def handle(self, context: HandlerContext) -> None:
        message = _incoming_message(context.current_update)
        if message is None:
            return

        lm = context.languages
        lang = self.resolve_language(context, message)
        channel_id = resolve_linked_chat_id(context, message)
        if channel_id is None:
            log.debug("Ignoring /connect outside an eligible target chat")
            return

        token = message_helper.single_argument(message)
        if token is None:
            await self.send_html(context, message, lm.get(lang, "channel_connect", "usage"))
            return

        try:
            verification_code = int(token)
        except ValueError:
            await self.send_html(context, message, lm.get(lang, "channel_connect", "invalid_id"))
            return

        configurations = context.managers.chat_configurations
        configuration = configurations.get_chat_configuration_by_channel_link_verification_code(
            verification_code
        )
        if configuration is None:
            if configurations.get_chat_configuration_by_channel_link_id(channel_id) is not None:
                log.debug(
                    "Notification chat %s is already linked; ignoring duplicate /connect",
                    channel_id,
                )
                return
            await self.send_html(context, message, lm.get(lang, "channel_connect", "unknown_id"))
            return

        chat_id = configuration.chat_id
        try:
            configurations.link_channel(chat_id, channel_id, message_helper.topic_id(message))
        except DatabaseException as e:
            log.warning(
                "Failed to link notification chat %s to chat %s: %s", channel_id, chat_id, e
            )
            await self.send_html(context, message, lm.get(lang, "channel_connect", "save_failed"))
            return

        linked = lm.get(lang, "channel_connect", "linked_success", chat_id)
        await self.send_html(context, message, linked)
        await self.delete_command_message(context, message)
        await _refresh_configuration_menu(context, verification_code, chat_id)


def resolve_linked_chat_id(context: HandlerContext, message: Message) -> int | None:
    """Resolves the notification chat being linked: a channel, or a group whose sender is one of
    its moderators.

    Returns:
        The chat id, or None when the sender cannot link it.
    """
    chat = message.chat
    if chat.type is ChatType.CHANNEL:
        return chat.id
    if not message_helper.is_group(chat):
        return None

    if message.automatic_forward:
        if message.sender_chat is not None and message.sender_chat.type is ChatType.CHANNEL:
            return message.sender_chat.id
        origin = message.forward_origin
        if isinstance(origin, MessageOriginChannel):
            return origin.chat.id

    if message.from_user is not None:
        administrators = context.chat_admins.get_if_present(chat.id)
        if administrators is not None and any(
            admin.id == message.from_user.id and admin.is_moderator for admin in administrators
        ):
            return chat.id

    return None


async def _refresh_configuration_menu(
    context: HandlerContext, verification_code: int, chat_id: int
) -> None:
    """Updates the configuration menu that displayed the verification code, so the administrator
    sees the chat linked without having to reopen the settings.

    The link is already saved and confirmed by the time this runs; failing to edit the menu (it may
    have been deleted, or the session expired) is logged and otherwise ignored.
    """
    sessions = context.sessions.configuration
    session = sessions.find_by_channel_link_verification_code(verification_code)
    if session is None or session.chat_id != chat_id:
        return

    # The code has been used; it must not be redeemable again from the same menu.
    sessions.update_channel_link_verification_code(session.hash, None)
    menu = session.ephemeral_message_id if session.ephemeral else session.message_id
    if menu is None:
        return

    try:
        await configuration_handler.notify_channel_linked(context, session)
    except RPCError as e:
        log.debug("Could not refresh the configuration menu for chat %s: %s", chat_id, e)


def _incoming_message(update: Update) -> Message | None:
    """The message of a regular message update or of a channel post."""
    return update.message if update.message is not None else update.channel_post
