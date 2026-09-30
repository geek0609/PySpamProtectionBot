"""Greets a group when the bot is added to it or becomes able to be configured there."""

import logging

from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import RPCError
from pyrogram.types import ChatMember, User

from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.handlers.group import configuration_handler
from spb.objects.context.handler_context import HandlerContext
from spb.utilities import message_helper

log = logging.getLogger(__name__)

JOIN_CACHE_PREFIX = "bot-membership:"


@update_handler(UpdateType.MY_CHAT_MEMBER)
class BotMembershipHandler(Handler):
    """Tells a group what to do next when the bot joins it or gains the permission it needs.

    Without Change Group Information the group is asked for it; with it, whoever made the change is
    shown the settings menu, the same one /settings opens.

    Adding the bot through a startgroup link also sends /start with a payload; claim_join() lets
    that command stand down, so the group is not told twice.
    """

    async def handle(self, context: HandlerContext) -> None:
        change = context.current_update.my_chat_member
        assert change is not None
        if not message_helper.is_group(change.chat):
            return

        joined = not _is_present(change.old_chat_member) and _is_present(change.new_chat_member)
        configurable = configuration_handler.can_change_info(change.new_chat_member)
        became_configurable = configurable and not configuration_handler.can_change_info(
            change.old_chat_member
        )
        if not joined and not became_configurable:
            return

        chat_id = change.chat.id
        context.cache.put(f"{JOIN_CACHE_PREFIX}{chat_id}", True)
        lm = context.languages
        lang = context.managers.language_preferences.get_chat_language(chat_id)

        if not configurable:
            await _send(context, chat_id, lm.get(lang, "bot_added", "permissions_required"))
            return

        if not await _open_settings(context, chat_id, change.from_user):
            await _send(context, chat_id, lm.get(lang, "bot_added", "ready"))


def claim_join(context: HandlerContext, chat_id: int) -> bool:
    """Reports whether the bot's arrival in the chat was just announced, consuming that record, so
    the startgroup /start that arrives with it can stay silent."""
    key = f"{JOIN_CACHE_PREFIX}{chat_id}"
    if context.cache.get_if_present(key) is None:
        return False
    context.cache.remove(key)
    return True


async def _open_settings(context: HandlerContext, chat_id: int, user: User | None) -> bool:
    """Opens the settings menu for whoever added or promoted the bot, when they may configure the
    chat themselves.

    Returns:
        True when the menu was shown.
    """
    if user is None or user.is_bot:
        return False

    await Handler.refresh_administrators(context, chat_id)
    if not Handler.is_change_information_administrator(context, chat_id, user.id):
        return False

    session = context.sessions.configuration.create_ephemeral(user.id, chat_id)
    try:
        await configuration_handler.open_main_menu(context, session)
    except RPCError as e:
        log.warning("Could not open the settings menu for %s in chat %s: %s", user.id, chat_id, e)
        return False
    return True


async def _send(context: HandlerContext, chat_id: int, html: str) -> None:
    await Handler.execute(
        "bot-added",
        context.telegram_client.send_message,
        chat_id=chat_id,
        text=html,
        parse_mode=ParseMode.HTML,
    )


def _is_present(member: ChatMember | None) -> bool:
    """Whether a membership state means the bot is in the chat."""
    return member is not None and member.status not in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.BANNED,
    )
