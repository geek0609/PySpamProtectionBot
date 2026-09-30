"""The /start command."""

from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, Message

from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.handlers.group import bot_membership_handler, configuration_handler
from spb.objects.context.configuration_context import ConfigurationContext
from spb.objects.context.handler_context import HandlerContext
from spb.objects.language import Language
from spb.utilities import html_escape, message_helper, start_screen


@update_handler(UpdateType.COMMAND, commands=("start",))
class StartHandler(Handler):
    """The /start command handler.

    In private chats the handler shows a getting-started message with an Add to Group button that
    uses the startgroup deep link to prompt the user to add the bot to a group, as well as a Set
    Language button that opens the language selection.

    In group chats /start is answered non-ephemerally and the content depends on who called it:

    - Change-information administrators see the getting-started paragraph and a Configure button
      linking to a private configuration session.
    - Other members only see the bot's own information, without the getting-started paragraph or
      any buttons.
    - With a payload (from a startgroup deep link) the bot was just added to the group and the
      configuration menu is opened immediately when the bot is a change-information administrator.

    All display strings are resolved at runtime from the bot's own identity and the Federation
    server (server name, host, and API version); nothing is hard-coded.
    """

    def accepts(self, context: HandlerContext) -> bool:
        # A forwarded /start is somebody quoting the command, not invoking it.
        message = context.current_update.message
        return message is not None and message.forward_origin is None

    async def handle(self, context: HandlerContext) -> None:
        message = context.current_update.message
        assert message is not None
        lang = self.resolve_language(context, message)
        if message_helper.is_private(message.chat):
            await _handle_private_start(context, message, lang)
        elif message_helper.is_group(message.chat):
            await _handle_group_start(context, message, lang)


async def _handle_private_start(context: HandlerContext, message: Message, lang: Language) -> None:
    """Sends the start screen, or opens the configuration menu a group's Configure button links
    to."""
    payload = message_helper.get_message_payload(message)
    if payload is not None:
        session = context.sessions.configuration.find(payload)
        if session is None or session.user_id != message.from_user.id:
            expired = context.languages.get(lang, "start", "session_expired_private")
            await Handler.reply_html(context, "start-session-expired", message, expired)
            return
        await configuration_handler.open_main_menu(context, session)
        return

    html = await start_screen.html(context, lang)
    markup = start_screen.markup(context, lang, message.from_user.id)
    await Handler.reply_html(context, "private-getting-started", message, html, markup)


async def _handle_group_start(context: HandlerContext, message: Message, lang: Language) -> None:
    """Answers /start in a group with the bot's summary, whose content and buttons depend on
    whether the caller can configure the bot, or opens the configuration menu right away after the
    bot was added through a startgroup deep link."""
    # Checked against Telegram rather than the cache: /start is typically sent right after the
    # bot is added or promoted, exactly when a cached administrator list is out of date.
    await Handler.refresh_administrators(context, message.chat.id)

    if message_helper.get_message_payload(message) is not None:
        await _handle_start_group_join(context, message, lang)
        return

    administrator = Handler.is_author_change_information_administrator(context, message)
    configurable = administrator and Handler.is_bot_change_information_administrator(
        context, message.chat.id
    )
    session = (
        context.sessions.configuration.create(message.from_user.id, message.chat.id)
        if configurable
        else None
    )
    await _send_getting_started(context, message, session, lang, administrator)


async def _handle_start_group_join(
    context: HandlerContext, message: Message, lang: Language
) -> None:
    """Opens the configuration menu when the bot can be configured, and otherwise asks for the bot
    to be promoted first."""
    # Joining through the link also reports the bot's new membership, which has already told the
    # group what to do next.
    if bot_membership_handler.claim_join(context, message.chat.id):
        return

    if not Handler.is_bot_change_information_administrator(context, message.chat.id):
        promote = context.languages.get(lang, "start", "startgroup_promote")
        await Handler.send_html(context, message, promote, True)
        return

    if message.from_user is None:
        return

    session = context.sessions.configuration.create(message.from_user.id, message.chat.id)
    await configuration_handler.open_main_menu(context, session)


def _build_getting_started_markup(
    context: HandlerContext, session: ConfigurationContext, lang: Language
) -> InlineKeyboardMarkup | None:
    """A Configure button that opens the session in a private chat, or None when the bot has no
    username to link to."""
    if not context.bot_username.strip():
        return None
    url = f"https://t.me/{context.bot_username}?start={session.hash}"
    configure = context.languages.get(lang, "buttons", "configure")
    return message_helper.single_row_markup(message_helper.url_button(configure, url))


async def _send_getting_started(
    context: HandlerContext,
    message: Message,
    session: ConfigurationContext | None,
    lang: Language,
    administrator: bool,
) -> None:
    """Sends the bot's summary as a regular group message.

    Change-information administrators also see the getting-started paragraph: the configurable
    variant with a Configure button when there is a session, or the permissions variant otherwise.
    Ordinary members see only the bot's own information and no buttons.
    """
    lm = context.languages
    html = [
        f"<b>{html_escape.escape(context.bot_name)}</b>\n\n",
        lm.get(lang, "start", "private_description"),
    ]
    if administrator:
        html.append(lm.get(lang, "start", "getting_started_header"))
        if session is not None:
            html.append(lm.get(lang, "start", "getting_started_group_configurable"))
        else:
            html.append(lm.get(lang, "start", "getting_started_group_permissions"))
    html.append(lm.get(lang, "start", "additional_help_header"))
    html.append(lm.get(lang, "start", "additional_help_body"))

    server = await message_helper.server_information(context)
    if server is not None:
        host = html_escape.escape(message_helper.server_host(context))
        api_version = html_escape.escape(server.api_version)
        html += [
            lm.get(lang, "start", "server_information_header"),
            lm.get(lang, "start", "server_name_label", html_escape.escape(server.name)),
            lm.get(lang, "start", "server_host_label", host),
            lm.get(lang, "start", "server_api_version_label", api_version),
        ]

    await Handler.execute(
        "getting-started",
        context.telegram_client.send_message,
        chat_id=message.chat.id,
        text="".join(html),
        parse_mode=ParseMode.HTML,
        message_thread_id=message_helper.topic_id(message),
        reply_markup=(
            _build_getting_started_markup(context, session, lang) if session is not None else None
        ),
    )
