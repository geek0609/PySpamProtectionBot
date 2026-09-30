"""The /settings command."""

from pyrogram.enums import ParseMode
from pyrogram.types import Message, ReplyParameters

from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.handlers.group import configuration_handler
from spb.handlers.secretary import secretary_settings_handler
from spb.objects.context.handler_context import HandlerContext
from spb.utilities import message_helper


@update_handler(UpdateType.COMMAND, commands=("settings",))
class SettingsHandler(Handler):
    """Opens the group configuration menu in groups, and the secretary settings in private chats."""

    async def handle(self, context: HandlerContext) -> None:
        message = context.current_update.message
        assert message is not None
        if message_helper.is_group(message.chat):
            await _handle_group_settings(context, message)
        elif message_helper.is_private(message.chat):
            await _handle_private_settings(context, message)


async def _handle_group_settings(context: HandlerContext, message: Message) -> None:
    """Opens the settings menu for administrators who can change the group's information.

    Anyone else is told so in an ephemeral reply. A command sent on behalf of a chat (an anonymous
    administrator or a channel) cannot be answered ephemerally or tied to a person, so it gets a
    regular reply, without buttons, asking for the command from a personal account. The settings
    menu is sent as an ephemeral message only the sender sees, and the bot must be such an
    administrator too for it to open."""
    # Checked against Telegram rather than the cache, so a permission just granted or revoked
    # applies to this very command instead of after the cached list expires.
    await Handler.refresh_administrators(context, message.chat.id)

    if not Handler.is_author_change_information_administrator(context, message):
        lang = Handler.resolve_language(context, message)
        if message.from_user is None or message.sender_chat is not None:
            await Handler.reply_html(
                context,
                "settings-anonymous-sender",
                message,
                context.languages.get(lang, "settings", "anonymous_sender"),
            )
            return
        await Handler.send_html(
            context, message, context.languages.get(lang, "settings", "admin_required"), True
        )
        return

    if not Handler.is_bot_change_information_administrator(context, message.chat.id):
        lang = Handler.resolve_language(context, message)
        await context.telegram_client.send_message(
            chat_id=message.chat.id,
            text=context.languages.get(lang, "settings", "bot_admin_required"),
            parse_mode=ParseMode.HTML,
            message_thread_id=message_helper.topic_id(message),
            reply_parameters=ReplyParameters(message_id=message.id),
            ephemeral_message_parameters=message_helper.ephemeral_to(message.from_user.id),
        )
        return

    session = context.sessions.configuration.create_ephemeral(message.from_user.id, message.chat.id)
    await configuration_handler.open_main_menu(context, session)


async def _handle_private_settings(context: HandlerContext, message: Message) -> None:
    """Opens the secretary settings for a user with secretary mode enabled."""
    user_id = message.from_user.id
    if context.managers.secretary_configurations.secretary_configuration_exists(user_id):
        session = context.sessions.configuration.create(user_id, message.chat.id)
        await secretary_settings_handler.open_menu(context, session)
        return

    lang = Handler.resolve_language(context, message)
    await Handler.send_html(
        context, message, context.languages.get(lang, "settings", "secretary_not_enabled")
    )
