"""The base class every handler extends."""

import contextlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from pyrogram.enums import ChatMembersFilter, ChatMemberStatus, ChatType, ParseMode
from pyrogram.errors import RPCError
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    ReplyParameters,
)

from spb.classes.cache import LoadFailedException
from spb.classes.federation_web_links import FederationWebLinks
from spb.objects.admin_info import AdminInfo
from spb.objects.context.configuration_context import ConfigurationContext
from spb.objects.context.handler_context import HandlerContext
from spb.objects.language import Language
from spb.utilities import html_escape, message_helper

log = logging.getLogger(__name__)


class Handler(ABC):
    """The base class every handler extends.

    A handler declares what it serves with update_handler() and implements handle(); the registry
    discovers it and the dispatcher invokes it on a worker task. Handlers never construct the
    services they use: the HandlerContext carries the Telegram client, the database and its
    managers, the configuration, the Federation server, the translations, the open dialogs, the
    shared caches, and the bot's own identity.

    One instance per handler class is shared by every worker task, so implementations must be
    stateless: keep per-update state in local variables, and put anything that must outlive a
    single update in the database, a session manager, or the context's cache.

    The static helpers below are the operations nearly every handler needs: permission checks,
    language resolution, replies (ephemeral in groups, regular in private chats), callback
    acknowledgement, and message editing. The ones non-handler code needs too live in
    message_helper.
    """

    @abstractmethod
    async def handle(self, context: HandlerContext) -> None:
        """Processes the update carried by the context.

        Called on a worker task, only when the handler's declared filters and accepts() both
        matched. Implementations may raise: the dispatcher logs the failure and moves on, so one
        misbehaving handler can never stall the bot. They must however not wait indefinitely, as a
        stuck worker is a worker the queue cannot use.
        """

    def accepts(self, context: HandlerContext) -> bool:
        """Narrows the declared match at runtime.

        The declaration expresses what can be decided from the shape of the update alone. Anything
        that needs the update's content or the bot's state, "only in group chats", "only from an
        administrator", "only while a session is open", is decided here instead. Returning False
        leaves the update to the next handler in priority order.
        """
        return True

    @property
    def name(self) -> str:
        """The name used for this handler in log messages."""
        return type(self).__name__

    @staticmethod
    def with_regular_group_reply_note(
        context: HandlerContext, lang: Language, message: Message, section: str, html: str
    ) -> str:
        """Appends an explanation to a reply-based command's answer when it was sent in a regular
        group without a reply.

        Unlike a supergroup, a regular group keeps no shared history: when the command replies to
        a message sent before the bot joined, Telegram delivers it with no trace of the reply at
        all. Such a command cannot be told apart from one that was never a reply, so the answer
        says why the reply may have been missed instead of only repeating the usage.

        Args:
            section: The command's localization section, which holds its regular_group_note.
        """
        if message.chat.type is not ChatType.GROUP:
            return html
        return html + context.languages.get(lang, section, "regular_group_note")

    @staticmethod
    def is_author_change_information_administrator(
        context: HandlerContext, message: Message
    ) -> bool:
        """Returns whether the message's author is an administrator of the message's chat with the
        Change Group Information permission, according to the cached administrator list."""
        if message.from_user is None:
            return False
        return Handler.is_change_information_administrator(
            context, message.chat.id, message.from_user.id
        )

    @staticmethod
    def is_change_information_administrator(
        context: HandlerContext, chat_id: int, user_id: int
    ) -> bool:
        """Returns whether the user is an administrator of the chat with the Change Group
        Information permission, according to the cached administrator list."""
        return Handler.has_administrator(
            context, chat_id, user_id, lambda admin: admin.can_change_info
        )

    @staticmethod
    def is_chat_administrator(context: HandlerContext, chat_id: int, user_id: int) -> bool:
        """Returns whether the user is a cached moderator of the chat: the owner, or an
        administrator who can delete messages or restrict members."""
        return Handler.has_administrator(
            context, chat_id, user_id, lambda admin: admin.is_moderator
        )

    @staticmethod
    def is_bot_change_information_administrator(context: HandlerContext, chat_id: int) -> bool:
        """Returns whether the bot itself is an administrator of the chat with the Change Group
        Information permission, according to the cached administrator list."""
        return Handler.is_change_information_administrator(context, chat_id, context.bot_user_id)

    @staticmethod
    def has_administrator(
        context: HandlerContext, chat_id: int, user_id: int, permission: Callable[[AdminInfo], bool]
    ) -> bool:
        """Returns whether the cached administrator list of a chat holds the user with the given
        permission. Call refresh_administrators() first when the user is acting on the result
        directly and a promotion or demotion must be honoured at once."""
        administrators = context.chat_admins.get_if_present(chat_id)
        return administrators is not None and any(
            admin.id == user_id and permission(admin) for admin in administrators
        )

    @staticmethod
    async def refresh_administrators(context: HandlerContext, chat_id: int) -> None:
        """Fetches the administrator list of a chat from Telegram, bypassing the cache, and stores
        it in the cache for everything that follows.

        Used before permission checks the user acts on directly, such as opening the settings menu,
        so a promotion or demotion is honoured at once instead of after the cached list expires.
        When the fetch fails the cached list, if any, is left in place and used instead.
        """
        # A failure is already logged by the loader.
        with contextlib.suppress(LoadFailedException):
            context.chat_admins.put(chat_id, await Handler.load_administrators(context, chat_id))

    @staticmethod
    async def load_administrators(context: HandlerContext, chat_id: int) -> list[AdminInfo]:
        """Fetches every administrator of the chat from Telegram, with the permissions each holds.

        The result fills the context's chat_admins, which drives permission checks, moderation
        eligibility and private moderator-notification delivery without per-check API calls.

        Raises:
            LoadFailedException: If the fetch failed, so the failure is not cached as an empty
                list.
        """
        try:
            members = [
                member
                async for member in context.telegram_client.get_chat_members(
                    chat_id, filter=ChatMembersFilter.ADMINISTRATORS
                )
            ]
        except Exception as e:
            # Any failure of the call itself, not only an API error, must stay uncached.
            log.warning("Failed to load administrators for chat %s: %s", chat_id, e)
            raise LoadFailedException(f"Failed to load administrators for chat {chat_id}") from e

        administrators = []
        for member in members:
            if member.status is ChatMemberStatus.OWNER:
                administrators.append(AdminInfo(member.user.id, True, True, True, True))
            elif member.status is ChatMemberStatus.ADMINISTRATOR:
                rights = member.privileges
                if rights is None:
                    # An administrator of a basic group holds every right, which the Bot API
                    # reports as such and MTProto leaves unstated.
                    administrators.append(AdminInfo(member.user.id, False, True, True, True))
                else:
                    administrators.append(
                        AdminInfo(
                            member.user.id,
                            False,
                            bool(rights.can_delete_messages),
                            bool(rights.can_restrict_members),
                            bool(rights.can_change_info),
                        )
                    )
        return administrators

    @staticmethod
    def resolve_language(context: HandlerContext, source: Message | CallbackQuery) -> Language:
        """Resolves the effective language for a message or a button press: the chat language in
        group chats, the user language in private chats."""
        if isinstance(source, CallbackQuery):
            message, user = source.message, source.from_user
        else:
            message, user = source, source.from_user

        preferences = context.managers.language_preferences
        if message is not None and message_helper.is_group(message.chat):
            return preferences.get_chat_language(message.chat.id)
        if user is not None:
            return preferences.get_user_language(user.id)
        return context.languages.default_language

    @staticmethod
    def session_language(context: HandlerContext, user_id: int) -> Language:
        """Resolves the effective language for a session owner."""
        return context.managers.language_preferences.get_user_language(user_id)

    @staticmethod
    async def send_html(
        context: HandlerContext,
        message: Message,
        html: str,
        ephemeral: bool = False,
        markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Sends an HTML answer to the message's chat, in the same forum topic.

        In group chats the answer is ephemeral, so only the message's author can see it; in private
        chats a regular message is sent unless an ephemeral one is asked for.
        """
        ephemeral_parameters = None
        if (ephemeral or message_helper.is_group(message.chat)) and message.from_user is not None:
            ephemeral_parameters = message_helper.ephemeral_to(message.from_user.id)

        await context.telegram_client.send_message(
            chat_id=message.chat.id,
            text=html,
            parse_mode=ParseMode.HTML,
            message_thread_id=message_helper.topic_id(message),
            ephemeral_message_parameters=ephemeral_parameters,
            reply_markup=markup,
        )

    @staticmethod
    async def send_ephemeral(context: HandlerContext, message: Message, text: str) -> None:
        """Sends a plain-text ephemeral message only the message's author can see."""
        if message.from_user is None:
            return

        await context.telegram_client.send_message(
            chat_id=message.chat.id,
            text=text,
            parse_mode=ParseMode.DISABLED,
            message_thread_id=message_helper.topic_id(message),
            ephemeral_message_parameters=message_helper.ephemeral_to(message.from_user.id),
        )

    @staticmethod
    async def send_ephemeral_or_regular(
        context: HandlerContext, message: Message, text: str, ephemeral: bool
    ) -> None:
        """Sends a plain-text ephemeral message, or a regular HTML answer when not ephemeral."""
        if ephemeral:
            await Handler.send_ephemeral(context, message, text)
        else:
            await Handler.send_html(context, message, text)

    @staticmethod
    async def delete_command_message(context: HandlerContext, message: Message) -> None:
        """Deletes a command message, unless it is ephemeral."""
        if message.ephemeral_message_id is not None:
            return
        try:
            await context.telegram_client.delete_messages(message.chat.id, message.id)
        except RPCError as e:
            log.debug("Could not delete command message in chat %s: %s", message.chat.id, e)

    @staticmethod
    async def execute[**P, T](
        label: str, method: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs
    ) -> T:
        """Calls a Telegram method, logging its arguments beforehand and again if Telegram rejects
        it.

        Args:
            label: A short label identifying the call in the logs.
            method: The client method to call.

        Raises:
            RPCError: If the call fails; its arguments have already been logged.
        """
        message_helper.log_outgoing(label, kwargs)
        try:
            return await method(*args, **kwargs)
        except RPCError as e:
            message_helper.log_failed(label, kwargs, e)
            raise

    @staticmethod
    async def try_execute[**P, T](
        label: str, method: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs
    ) -> None:
        """Calls a Telegram method on a best-effort basis: a failure is logged, as by execute(),
        and otherwise ignored."""
        with contextlib.suppress(RPCError):
            await Handler.execute(label, method, *args, **kwargs)

    @staticmethod
    async def reply_html(
        context: HandlerContext,
        label: str,
        message: Message,
        html: str,
        markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        """Sends an HTML reply to a message, in the same chat and forum topic. Unlike send_html(),
        the reply is never ephemeral."""
        return await Handler.execute(
            label,
            context.telegram_client.send_message,
            chat_id=message.chat.id,
            text=html,
            parse_mode=ParseMode.HTML,
            reply_parameters=ReplyParameters(message_id=message.id),
            message_thread_id=message_helper.topic_id(message),
            reply_markup=markup,
        )

    @staticmethod
    async def answer(
        context: HandlerContext, callback_query: CallbackQuery, text: str | None = None
    ) -> None:
        """Answers the button press, stopping its loading indicator, with an optional toast shown
        at the top of the chat.

        Telegram shows toasts as plain text, so any HTML in the text is converted to the text it
        would display rather than being shown as raw tags.
        """
        await context.telegram_client.answer_callback_query(
            callback_query.id,
            text=html_escape.to_plain_text(text) if text is not None else None,
            show_alert=False,
        )

    @staticmethod
    async def answer_alert(
        context: HandlerContext, callback_query: CallbackQuery, text: str
    ) -> None:
        """Answers the button press with an alert dialog shown in the middle of the screen, used
        for errors or state changes that require the user's attention.

        Telegram shows alerts as plain text, so any HTML in the text is converted to the text it
        would display rather than being shown as raw tags.
        """
        await context.telegram_client.answer_callback_query(
            callback_query.id, text=html_escape.to_plain_text(text), show_alert=True
        )

    @staticmethod
    async def edit_message(
        context: HandlerContext,
        message: Message,
        callback_query: CallbackQuery,
        html: str,
        markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Edits a message to show new HTML text and an optional keyboard, editing it as an
        ephemeral message when it is one."""
        if message.ephemeral_message_id is not None and callback_query.from_user is not None:
            await context.telegram_client.edit_ephemeral_message_text(
                chat_id=message.chat.id,
                receiver_user_id=callback_query.from_user.id,
                ephemeral_message_id=message.ephemeral_message_id,
                text=html,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            return

        await context.telegram_client.edit_message_text(
            chat_id=message.chat.id,
            message_id=message.id,
            text=html,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )

    @staticmethod
    async def edit_session_message(
        context: HandlerContext,
        message: Message,
        callback_query: CallbackQuery,
        session: ConfigurationContext,
        html: str,
        markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Edits a configuration menu to show new HTML text and an optional keyboard.

        When the session is ephemeral, the edit uses the session's ephemeral message and owner
        rather than the button press's message, because the configuration menu may be sent as an
        ephemeral message in a group chat.
        """
        if session.ephemeral and session.ephemeral_message_id is not None:
            await context.telegram_client.edit_ephemeral_message_text(
                chat_id=message.chat.id,
                receiver_user_id=session.user_id,
                ephemeral_message_id=session.ephemeral_message_id,
                text=html,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            return

        await Handler.edit_message(context, message, callback_query, html, markup)

    @staticmethod
    async def edit_to_expired(
        context: HandlerContext,
        callback_query: CallbackQuery,
        section: str = "configuration",
        key: str = "session_expired",
    ) -> None:
        """Edits the message carrying the button to say that the session behind it has expired,
        a configuration session unless another translation is named."""
        message = callback_query.message
        if message is None or callback_query.from_user is None:
            return

        expired = context.languages.get(
            Handler.resolve_language(context, callback_query), section, key
        )
        await Handler.edit_message(context, message, callback_query, expired)

    @staticmethod
    async def remove_inline_keyboard(context: HandlerContext, message: Message) -> None:
        """Removes the action buttons from a message on a best-effort basis, leaving its text and
        any link buttons intact.

        Used when a notification's buttons can no longer be acted on, so the information the
        notification carries, and its link to the Federation Web Application, stay available to
        the reader.
        """
        await Handler.try_execute(
            "remove-inline-keyboard",
            context.telegram_client.edit_message_reply_markup,
            chat_id=message.chat.id,
            message_id=message.id,
            reply_markup=FederationWebLinks.links_only(message.reply_markup),
        )
