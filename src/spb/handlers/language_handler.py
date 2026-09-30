"""The /language command and its picker."""

import logging

from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.exceptions import DatabaseException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.language import Language
from spb.utilities import message_helper, start_screen

log = logging.getLogger(__name__)

CALLBACK_PREFIX = "lang"
"""The callback namespace of the language picker.

The start screen puts a Set Language button on its own message, so the namespace is a contract
between the two rather than an internal detail."""


@update_handler(
    UpdateType.COMMAND,
    UpdateType.CALLBACK_QUERY,
    commands=("language", "lang"),
    callback_data=(f"{CALLBACK_PREFIX}:",),
)
class LanguageHandler(Handler):
    """Lets users choose the bot's language, and group administrators the group's.

    In private chats the command shows the current language and a flag button per language; in
    groups only administrators who can change the group's information may change the language,
    checked against Telegram on both the command and each button press, and the menu is an
    ephemeral message only the caller sees.

    lang:<language_code> buttons save the new language and refresh the menu, while lang:menu,
    lang:start_menu and lang:start re-open the picker or return to the private /start screen.
    """

    async def handle(self, context: HandlerContext) -> None:
        update = context.current_update
        if update.callback_query is not None:
            await self._handle_callback(context, update.callback_query)
            return

        message = update.message
        if message is None or message.from_user is None:
            return

        lm = context.languages
        preferences = context.managers.language_preferences
        chat_id = message.chat.id
        user_id = message.from_user.id

        if message_helper.is_group(message.chat):
            await self.refresh_administrators(context, chat_id)
            if not self.is_author_change_information_administrator(context, message):
                admin_only = lm.get(
                    preferences.get_user_language(user_id), "general", "language.group_admin_only"
                )
                await self.send_html(context, message, admin_only, True)
                return
            current = preferences.get_chat_language(chat_id)
            await self.send_html(
                context, message, _select_html(context, current), True, _markup(context)
            )
            return

        current = preferences.get_user_language(user_id)
        await self.reply_html(
            context,
            "language-menu",
            message,
            _select_html(context, current),
            _private_markup(context, current, False),
        )

    async def _handle_callback(
        self, context: HandlerContext, callback_query: CallbackQuery
    ) -> None:
        parts = callback_query.data.split(":", 2)
        message = callback_query.message
        if len(parts) < 2 or message is None:
            await self.answer(context, callback_query)
            return

        action = parts[1]
        if action in ("start", "start_menu", "menu"):
            if action != "menu" and not message_helper.is_private(message.chat):
                await self.answer(context, callback_query)
                return
            current = self.resolve_language(context, callback_query)
            if action == "start":
                html = await start_screen.html(context, current)
                markup = start_screen.markup(context, current, callback_query.from_user.id)
            elif action == "start_menu":
                html, markup = (
                    _select_html(context, current),
                    _private_markup(context, current, True),
                )
            else:
                html, markup = _select_html(context, current), _markup(context)
            await self.edit_message(context, message, callback_query, html, markup)
            await self.answer(context, callback_query)
            return

        # A button from an older menu may name a language that is no longer shipped.
        language = context.languages.resolve(action)
        if language is None:
            await self.answer(context, callback_query)
            return

        lm = context.languages
        chat_id = message.chat.id
        user_id = callback_query.from_user.id
        in_group = chat_id < 0
        if in_group:
            await self.refresh_administrators(context, chat_id)
            if not self.is_change_information_administrator(context, chat_id, user_id):
                admin_only = lm.get(
                    self.resolve_language(context, callback_query),
                    "general",
                    "language.group_admin_only",
                )
                await self.answer_alert(context, callback_query, admin_only)
                return

        preferences = context.managers.language_preferences
        try:
            if in_group:
                preferences.set_chat_language(chat_id, language)
            else:
                preferences.set_user_language(user_id, language)
        except DatabaseException as e:
            log.warning("Failed to save language preference: %s", e)
            error = lm.get(language, "general", "error.occurred")
            await self.answer_alert(context, callback_query, error)
            return

        changed = lm.get(language, "general", "language.changed", language.emoji, language.name)
        html = changed + "\n\n" + _select_html(context, language)
        return_to_start = len(parts) == 3 and parts[2] == "start"
        markup = (
            _markup(context) if in_group else _private_markup(context, language, return_to_start)
        )
        await self.edit_message(context, message, callback_query, html, markup)
        await self.answer(context, callback_query, changed)


def _select_html(context: HandlerContext, current: Language) -> str:
    """Asks for a language, naming the current one."""
    lm = context.languages
    return (
        lm.get(current, "general", "language.select")
        + "\n\n"
        + lm.get(current, "general", "language.current", current.emoji, current.name)
    )


def _private_markup(
    context: HandlerContext, lang: Language, from_start: bool
) -> InlineKeyboardMarkup:
    """The picker of a private chat, with a way back to the start screen when opened from it."""
    rows = _language_rows(context, ":start" if from_start else "")
    if from_start:
        back = context.languages.get(lang, "general", "back")
        rows.append([message_helper.button(back, f"{CALLBACK_PREFIX}:start")])
    return message_helper.markup(*rows)


def _markup(context: HandlerContext) -> InlineKeyboardMarkup:
    return message_helper.markup(*_language_rows(context, ""))


def _language_rows(
    context: HandlerContext, callback_suffix: str
) -> list[list[InlineKeyboardButton]]:
    """A flag button per available language, four to a row."""
    buttons = [
        message_helper.button(language.emoji, f"{CALLBACK_PREFIX}:{language.code}{callback_suffix}")
        for language in context.languages.available_languages
    ]
    return [buttons[index : index + 4] for index in range(0, len(buttons), 4)]
