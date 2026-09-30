"""The /help command and its menu."""

from pyrogram.types import InlineKeyboardMarkup

from spb.classes.handler import Handler
from spb.classes.language_manager import LanguageManager
from spb.classes.update_handler import update_handler
from spb.enums import HelpPage, OperatorPage, SettingsPage, UpdateType
from spb.objects.context.handler_context import HandlerContext
from spb.objects.language import Language
from spb.utilities import message_helper

CALLBACK_PREFIX = "help"

_CATEGORIES = (
    HelpPage.SECRETARY_MODE,
    HelpPage.CHAT_PROTECTION,
    HelpPage.SETTINGS,
    HelpPage.FEDERATION_COMMANDS,
    HelpPage.OPERATORS,
)


@update_handler(
    UpdateType.COMMAND,
    UpdateType.CALLBACK_QUERY,
    commands=("help",),
    callback_data=(f"{CALLBACK_PREFIX}:",),
)
class HelpHandler(Handler):
    """Explains the bot, one feature at a time.

    /help sends the main help menu with a button per category, and each button updates the message
    to show that category. help:<PAGE> selects a top-level page; help:SETTINGS and help:OPERATORS
    open a sub-menu of their own topics, help:SETTINGS:<SETTING> and help:OPERATORS:<TOPIC> select
    one of those topics, and help:MAIN returns to the main menu. In a group the menu is an
    ephemeral message only the caller sees, and its buttons edit that message in place.
    """

    async def handle(self, context: HandlerContext) -> None:
        update = context.current_update
        if update.callback_query is not None:
            await self._handle_callback(context)
            return

        message = update.message
        if message is None or not (
            message_helper.is_private(message.chat) or message_helper.is_group(message.chat)
        ):
            return

        lm = context.languages
        lang = self.resolve_language(context, message)
        html = _main_menu_html(lm, lang)
        markup = _main_menu_markup(lm, lang)

        if message_helper.is_group(message.chat):
            # Shown only to whoever asked, so browsing the menu does not fill the group.
            await self.send_html(context, message, html, True, markup)
            return
        await self.reply_html(context, "help-main-menu", message, html, markup)

    async def _handle_callback(self, context: HandlerContext) -> None:
        callback_query = context.current_update.callback_query
        assert callback_query is not None
        action = callback_query.data.partition(":")[2]
        message = callback_query.message
        if message is None:
            await self.answer(context, callback_query)
            return

        lm = context.languages
        lang = self.resolve_language(context, callback_query)
        if action == "SETTINGS":
            html, markup = _settings_menu_html(lm, lang), _submenu_markup(lm, lang, SettingsPage)
        elif action.startswith("SETTINGS:"):
            page = SettingsPage[action.removeprefix("SETTINGS:")]
            html, markup = _page_html(lm, lang, page), _back_markup(lm, lang, "SETTINGS")
        elif action == "OPERATORS":
            html = _page_html(lm, lang, HelpPage.OPERATORS)
            markup = _submenu_markup(lm, lang, OperatorPage)
        elif action.startswith("OPERATORS:"):
            operator_page = OperatorPage[action.removeprefix("OPERATORS:")]
            html, markup = _page_html(lm, lang, operator_page), _back_markup(lm, lang, "OPERATORS")
        elif action == "MAIN":
            html, markup = _main_menu_html(lm, lang), _main_menu_markup(lm, lang)
        else:
            help_page = HelpPage[action]
            html, markup = _page_html(lm, lang, help_page), _back_markup(lm, lang, "MAIN")

        await self.edit_message(context, message, callback_query, html, markup)
        await self.answer(context, callback_query)


def _main_menu_html(lm: LanguageManager, lang: Language) -> str:
    return (
        HelpPage.MAIN.body(lm, lang)
        + f"\n\n<b>{lm.get(lang, 'help', 'categories_header')}</b>\n"
        + lm.get(lang, "help", "categories_body")
    )


def _main_menu_markup(lm: LanguageManager, lang: Language) -> InlineKeyboardMarkup:
    """Offers a button per help category, two to a row."""
    buttons = [
        message_helper.button(page.title(lm, lang), f"{CALLBACK_PREFIX}:{page.name}")
        for page in _CATEGORIES
    ]
    return message_helper.markup(*message_helper.rows_of_two(buttons))


def _page_html(
    lm: LanguageManager, lang: Language, page: HelpPage | SettingsPage | OperatorPage
) -> str:
    """Renders a help page, titled unless its body brings its own bold title."""
    body = page.body(lm, lang)
    if body.startswith("<b>"):
        return body
    return f"<b>{page.title(lm, lang)}</b>\n\n{body}"


def _settings_menu_html(lm: LanguageManager, lang: Language) -> str:
    body = lm.get(lang, "help_pages", "settings_body")
    if body.startswith("<b>"):
        return body
    return f"<b>{lm.get(lang, 'help_pages', 'settings_title')}</b>\n\n{body}"


def _submenu_markup(
    lm: LanguageManager, lang: Language, pages: type[SettingsPage] | type[OperatorPage]
) -> InlineKeyboardMarkup:
    """Offers a button per topic of the settings or operators sub-menu, and a way back."""
    section = "SETTINGS" if pages is SettingsPage else "OPERATORS"
    buttons = [
        message_helper.button(page.title(lm, lang), f"{CALLBACK_PREFIX}:{section}:{page.name}")
        for page in pages
    ]
    buttons.append(
        message_helper.button(lm.get(lang, "general", "back"), f"{CALLBACK_PREFIX}:MAIN")
    )
    return message_helper.markup(*message_helper.rows_of_two(buttons))


def _back_markup(lm: LanguageManager, lang: Language, target: str) -> InlineKeyboardMarkup:
    """A single Back button returning to the given menu."""
    return message_helper.single_row_markup(
        message_helper.button(lm.get(lang, "general", "back"), f"{CALLBACK_PREFIX}:{target}")
    )
