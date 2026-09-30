"""The secretary settings menu and the owner's decisions about contacts."""

import logging

from pyrogram.enums import ParseMode
from pyrogram.errors import RPCError
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from spb.classes.federation_web_links import FederationWebLinks, Record
from spb.classes.handler import Handler
from spb.classes.language_manager import LanguageManager
from spb.classes.notifications import notification_formatter
from spb.classes.update_handler import update_handler
from spb.enums import ScanningBehavior, SecretaryContactStatus, UpdateType
from spb.exceptions import DatabaseException
from spb.handlers.group import report_handler
from spb.handlers.secretary import secretary_message_handler
from spb.objects.context.configuration_context import ConfigurationContext
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.secretary_configuration import SecretaryConfiguration
from spb.objects.database.secretary_contact import SecretaryContact
from spb.objects.language import Language
from spb.utilities import entity_info_renderer, message_helper, start_screen

log = logging.getLogger(__name__)

CALLBACK_PREFIX = "secset"
OPEN_CALLBACK = f"{CALLBACK_PREFIX}:open"
OPEN_WITH_BACK_CALLBACK = f"{CALLBACK_PREFIX}:openfromstart"
"""Opens the secretary menu with a Back button that returns to the /start screen.

The start screen puts this button on its own message, so it is a contract between the two rather
than an internal detail."""
CONTACT_CALLBACK_PREFIX = "seccontact"
CONTACT_SETTINGS_CALLBACK_PREFIX = "seccontactsettings"
REPORT_CALLBACK_PREFIX = "secreport"
"""Opens a report dialog for a first-contact message, from the Report button on its
notification."""

BUSINESS_START_PAYLOAD_PREFIX = "bizChat"
"""The deep-link payload Telegram uses for the "Manage Bot" button in business chats."""


@update_handler(
    UpdateType.COMMAND,
    UpdateType.CALLBACK_QUERY,
    commands=("start", "settings"),
    callback_data=(
        f"{CALLBACK_PREFIX}:",
        f"{CONTACT_CALLBACK_PREFIX}:",
        f"{CONTACT_SETTINGS_CALLBACK_PREFIX}:",
        f"{REPORT_CALLBACK_PREFIX}:",
    ),
    priority=100,
)
class SecretarySettingsHandler(Handler):
    """Handles the secretary settings menu and its content scanning toggle, the owner's
    per-connection contact decisions, and the Report button of first-contact notifications."""

    async def handle(self, context: HandlerContext) -> None:
        update = context.current_update
        callback_query = update.callback_query
        if callback_query is None:
            await _handle_command(context, update.message)
            return

        data: str = callback_query.data
        if data.startswith((f"{CONTACT_CALLBACK_PREFIX}:", f"{CONTACT_SETTINGS_CALLBACK_PREFIX}:")):
            await _handle_contact_decision(context, callback_query, data)
            return

        if data.startswith(f"{REPORT_CALLBACK_PREFIX}:"):
            await _handle_report(
                context, callback_query, data.removeprefix(f"{REPORT_CALLBACK_PREFIX}:")
            )
            return

        if data in (OPEN_CALLBACK, OPEN_WITH_BACK_CALLBACK):
            await _open_from_start(context, callback_query, data == OPEN_WITH_BACK_CALLBACK)
            return

        parts = data.split(":", 2)
        if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
            await self.answer(context, callback_query)
            return

        _, hash, action = parts
        session = context.sessions.configuration.find(hash)
        if session is None or session.user_id != callback_query.from_user.id:
            await self.answer(context, callback_query)
            await self.edit_to_expired(
                context, callback_query, "secretary_settings", "session_expired"
            )
            return

        message = callback_query.message
        if message is None:
            await self.answer(context, callback_query)
            return

        lang = self.session_language(context, session.user_id)
        if action == "start":
            html = await start_screen.html(context, lang)
            markup = start_screen.markup(context, lang, callback_query.from_user.id)
            await self.edit_message(context, message, callback_query, html, markup)
            await self.answer(context, callback_query)
            return

        if action.startswith("set_scanning:"):
            requested = action.removeprefix("set_scanning:")
            if requested not in ("on", "off"):
                log.warning("Unsupported secretary scanning state '%s'", requested)
                await self.answer(context, callback_query)
                return
            _set_scanning_enabled(context, session.user_id, requested == "on")

            lm = context.languages
            configuration = context.managers.secretary_configurations.resolve(session.user_id)
            html = _menu_html(lm, configuration, lang)
            markup = _menu_markup(lm, configuration, session, lang, session.show_back_button)
            await self.edit_message(context, message, callback_query, html, markup)
            toast = (
                "toast_scanning_enabled"
                if configuration.scanning_enabled
                else "toast_scanning_disabled"
            )
            await self.answer(context, callback_query, lm.get(lang, "secretary_settings", toast))
            return

        if not action.startswith("set_behavior:"):
            log.warning("Unknown secretary settings action '%s'", action)
            await self.answer(context, callback_query)
            return

        requested = action.removeprefix("set_behavior:")
        if requested not in ("PASSIVE", "STRICT"):
            log.warning("Unsupported secretary behavior '%s'", requested)
            await self.answer(context, callback_query)
            return
        _set_behavior(context, session.user_id, ScanningBehavior[requested])

        lm = context.languages
        configuration = context.managers.secretary_configurations.resolve(session.user_id)
        html = _menu_html(lm, configuration, lang)
        markup = _menu_markup(lm, configuration, session, lang, session.show_back_button)
        await self.edit_message(context, message, callback_query, html, markup)
        behavior = _behavior_display_name(lm, lang, configuration.behavior)
        changed = lm.get(lang, "secretary_settings", "toast_behavior_changed", behavior)
        await self.answer(context, callback_query, changed)

    def accepts(self, context: HandlerContext) -> bool:
        if context.current_update.callback_query is not None:
            return True

        # Secretary mode is a private-chat feature, and it only claims /start when the deep link
        # says the user arrived from their business account; every other /start belongs to
        # StartHandler, which this handler outranks.
        message = context.current_update.message
        if message is None or not message_helper.is_private(message.chat):
            return False

        if context.command_name == "start":
            payload = message_helper.get_message_payload(message)
            return payload is not None and payload.startswith(BUSINESS_START_PAYLOAD_PREFIX)

        return True


async def open_menu(context: HandlerContext, session: ConfigurationContext) -> None:
    """Sends the secretary settings menu to the session owner's private chat."""
    lm = context.languages
    configuration = context.managers.secretary_configurations.resolve(session.user_id)
    lang = Handler.session_language(context, session.user_id)
    sent = await Handler.execute(
        "open-secretary-settings",
        context.telegram_client.send_message,
        chat_id=session.user_id,
        text=_menu_html(lm, configuration, lang),
        parse_mode=ParseMode.HTML,
        reply_markup=_menu_markup(lm, configuration, session, lang, False),
    )
    context.sessions.configuration.update_message_id(session.hash, sent.id)


async def _handle_command(context: HandlerContext, message: Message | None) -> None:
    if message is None or message.from_user is None:
        return
    user_id = message.from_user.id
    lm = context.languages

    if not context.managers.secretary_configurations.secretary_configuration_exists(user_id):
        if context.command_name == "settings":
            lang = Handler.resolve_language(context, message)
            await Handler.send_html(
                context, message, lm.get(lang, "settings", "secretary_not_enabled")
            )
            return

        if _enable_from_business_deep_link(context, user_id):
            await _send_contact_deep_link_view(context, message)
            return

        lang = Handler.resolve_language(context, message)
        await Handler.send_html(context, message, lm.get(lang, "secretary_settings", "not_enabled"))
        return

    if context.command_name == "start":
        await _send_contact_deep_link_view(context, message)
        return

    session = context.sessions.configuration.create(user_id, message.chat.id)
    await open_menu(context, session)


async def _send_contact_deep_link_view(context: HandlerContext, message: Message) -> None:
    """Shows the owner what is known about the contact whose business chat they came from, with
    buttons to allow or deny them."""
    lang = Handler.resolve_language(context, message)
    lm = context.languages
    contact_id = _business_contact_id(message)

    if contact_id is None:
        not_known = lm.get(lang, "secretary_settings", "contact_not_known")
        await Handler.send_html(context, message, not_known, False, _open_settings_markup(lm, lang))
        return

    configuration = context.managers.secretary_configurations.resolve(message.from_user.id)
    connection_id = configuration.business_connection_id
    connected = connection_id is not None and connection_id.strip() != ""
    contact = (
        context.managers.secretary_contacts.get_secretary_contact(connection_id, contact_id)
        if connected
        else None
    )

    html = [lm.get(lang, "secretary_settings", "contact_title")]
    if contact is not None:
        status = lm.get(lang, "secretary_settings", f"contact_{contact.status.name.lower()}")
        html.append(lm.get(lang, "secretary_settings", "contact_status", status))
    else:
        html.append(lm.get(lang, "secretary_settings", "contact_not_known_hint"))
    entity_uuid = await entity_info_renderer.append_contact_info(context, html, contact_id, lang)

    markup = (
        secretary_message_handler.contact_settings_decision_markup(
            lm,
            connection_id,
            contact_id,
            lang,
            contact.status if contact is not None else SecretaryContactStatus.UNKNOWN,
        )
        if connected
        else _open_settings_markup(lm, lang)
    )
    markup = FederationWebLinks.attach(
        markup, context.web_links.button(lm, lang, Record.ENTITY, entity_uuid)
    )
    await Handler.send_html(context, message, "".join(html), False, markup)


def _open_settings_markup(lm: LanguageManager, lang: Language) -> InlineKeyboardMarkup:
    """A single button that opens the secretary settings."""
    label = lm.get(lang, "secretary_settings", "contact_not_known_button")
    return message_helper.single_row_markup(message_helper.button(label, OPEN_CALLBACK))


def _business_contact_id(message: Message) -> int | None:
    """The contact named by a business deep link, or None when the payload names none."""
    payload = message_helper.get_message_payload(message)
    if payload is None or not payload.startswith(BUSINESS_START_PAYLOAD_PREFIX):
        return None
    try:
        return int(payload.removeprefix(BUSINESS_START_PAYLOAD_PREFIX))
    except ValueError:
        return None


def _enable_from_business_deep_link(context: HandlerContext, user_id: int) -> bool:
    """Enables secretary mode for a user who arrived from their business account.

    Returns:
        False when the configuration could not be saved.
    """
    try:
        context.managers.secretary_configurations.create_secretary_configuration(user_id)
    except DatabaseException as e:
        log.warning(
            "Failed to enable secretary mode for user %s from the business deep link: %s",
            user_id,
            e,
        )
        return False
    return True


async def _open_from_start(
    context: HandlerContext, callback_query: CallbackQuery, show_back_button: bool
) -> None:
    """Replaces the message carrying the button with the secretary settings menu."""
    message = callback_query.message
    user_id = callback_query.from_user.id
    if (
        message is None
        or not message_helper.is_private(message.chat)
        or not context.managers.secretary_configurations.secretary_configuration_exists(user_id)
    ):
        await Handler.answer(context, callback_query)
        return

    sessions = context.sessions.configuration
    session = sessions.create(user_id, message.chat.id)
    sessions.update_message_id(session.hash, message.id)
    sessions.update_show_back_button(session.hash, show_back_button)
    lm = context.languages
    lang = Handler.session_language(context, user_id)
    configuration = context.managers.secretary_configurations.resolve(user_id)
    html = _menu_html(lm, configuration, lang)
    markup = _menu_markup(lm, configuration, session, lang, show_back_button)
    await Handler.edit_message(context, message, callback_query, html, markup)
    await Handler.answer(context, callback_query)


async def _handle_contact_decision(
    context: HandlerContext, callback_query: CallbackQuery, data: str
) -> None:
    """Allows or denies a contact from the buttons of an owner notification or a contact's
    settings page, then refreshes those buttons to show the new status."""
    parts = data.split(":", 3)
    message = callback_query.message
    settings_callback = parts[0] == CONTACT_SETTINGS_CALLBACK_PREFIX
    if (
        len(parts) != 4
        or (parts[0] != CONTACT_CALLBACK_PREFIX and not settings_callback)
        or (message is not None and not message_helper.is_private(message.chat))
    ):
        await Handler.answer(context, callback_query)
        return

    _, connection_id, contact, decision = parts
    user_id = callback_query.from_user.id
    configuration = context.managers.secretary_configurations.get_secretary_configuration(user_id)
    if configuration is None or connection_id != configuration.business_connection_id:
        log.warning(
            "Rejected secretary contact callback for connection %s from user %s",
            connection_id,
            user_id,
        )
        await Handler.answer(context, callback_query)
        return

    try:
        contact_id = int(contact)
    except ValueError:
        await Handler.answer(context, callback_query)
        return

    allowed = decision == "allow"
    if not allowed and decision != "deny":
        await Handler.answer(context, callback_query)
        return

    lm = context.languages
    lang = Handler.session_language(context, user_id)
    status = SecretaryContactStatus.ALLOWED if allowed else SecretaryContactStatus.DENIED
    contacts = context.managers.secretary_contacts
    client = context.telegram_client
    try:
        contacts.set_secretary_contact_status(connection_id, contact_id, status)
        updated = contacts.get_secretary_contact(connection_id, contact_id)
        if message is not None and settings_callback and updated is not None:
            decision_markup = secretary_message_handler.contact_settings_decision_markup(
                lm, connection_id, contact_id, lang, status
            )
            await client.edit_message_text(
                message.chat.id,
                message.id,
                await _contact_menu_html(context, updated, lang),
                parse_mode=ParseMode.HTML,
                reply_markup=FederationWebLinks.keep_links(decision_markup, message.reply_markup),
            )
        elif message is not None:
            decision_markup = secretary_message_handler.contact_decision_markup(
                lm,
                secretary_message_handler.ContactDecision(connection_id, contact_id, lang, status),
            )
            await client.edit_message_reply_markup(
                message.chat.id,
                message.id,
                FederationWebLinks.keep_links(
                    secretary_message_handler.keep_report_button(
                        decision_markup, message.reply_markup
                    ),
                    message.reply_markup,
                ),
            )
        toast = "toast_contact_allowed" if allowed else "toast_contact_denied"
        await Handler.answer(context, callback_query, lm.get(lang, "secretary_settings", toast))
    except DatabaseException as e:
        log.warning(
            "Failed to %s secretary contact %s in connection %s: %s",
            decision,
            contact_id,
            connection_id,
            e,
        )
        await Handler.answer_alert(
            context, callback_query, lm.get(lang, "general", "error.occurred")
        )
    except RPCError as e:
        log.warning(
            "Failed to remove secretary decision buttons for contact %s in connection %s: %s",
            contact_id,
            connection_id,
            e,
        )
        await Handler.answer_alert(
            context, callback_query, lm.get(lang, "general", "error.occurred")
        )


async def _handle_report(
    context: HandlerContext, callback_query: CallbackQuery, token: str
) -> None:
    """Opens a report dialog for the first-contact message behind a notification's Report button.

    The dialog is the same one a forwarded message opens, sent as a reply to the notification, so
    choosing the incident type, commenting and submitting are all handled by ReportHandler.
    """
    message = callback_query.message
    if message is None or not message_helper.is_private(message.chat):
        await Handler.answer(context, callback_query)
        return

    owner_id = callback_query.from_user.id
    lm = context.languages
    lang = Handler.session_language(context, owner_id)
    target = context.sessions.secretary_reports.find(token, owner_id)
    if target is None:
        await Handler.answer_alert(
            context, callback_query, lm.get(lang, "secretary", "report_expired")
        )
        return

    if not context.federation.is_available:
        await Handler.answer_alert(
            context, callback_query, lm.get(lang, "report", "federation_not_configured")
        )
        return

    if not context.federation.is_authenticated:
        await Handler.answer_alert(
            context, callback_query, lm.get(lang, "report", "federation_not_authorized")
        )
        return

    session = context.sessions.report.create_from_secretary(target)
    await report_handler.send_private_prompt(context, session, lang, message.id)
    await Handler.answer(context, callback_query)


async def _contact_menu_html(
    context: HandlerContext, contact: SecretaryContact, lang: Language
) -> str:
    """A contact's settings page: who they are, their status, and what Federation knows of them."""
    lm = context.languages
    status = lm.get(lang, "secretary_settings", f"contact_{contact.status.name.lower()}")
    mention = notification_formatter.user_id_mention(lm, lang, contact.id, context.managers.users)
    html = [
        lm.get(lang, "secretary_settings", "contact_title"),
        lm.get(lang, "secretary", "notification_contact", mention),
        lm.get(lang, "secretary_settings", "contact_status", status),
    ]
    await entity_info_renderer.append_contact_info(context, html, contact.id, lang)
    return "".join(html)


def _menu_html(lm: LanguageManager, configuration: SecretaryConfiguration, lang: Language) -> str:
    behavior = configuration.behavior
    selection = (
        f"{_behavior_display_name(lm, lang, behavior)}. "
        f"{lm.get(lang, 'secretary_settings', f'behavior_{behavior.name.lower()}_description')}"
    )
    return (
        lm.get(lang, "secretary_settings", "title")
        + lm.get(lang, "secretary_settings", "description")
        + "\n\n"
        + lm.get(lang, "secretary_settings", "behavior", selection)
        + "\n\n"
        + lm.get(
            lang, "secretary_settings", "scanning", _scanning_selection(lm, lang, configuration)
        )
        + "\n"
    )


def _scanning_selection(
    lm: LanguageManager, lang: Language, configuration: SecretaryConfiguration
) -> str:
    """The scanning line of the menu: whether content scanning is on and what that means."""
    if configuration.scanning_enabled:
        state = lm.get(lang, "general", "enabled")
        description = lm.get(lang, "secretary_settings", "scanning_enabled_description")
    else:
        state = lm.get(lang, "general", "disabled")
        description = lm.get(lang, "secretary_settings", "scanning_disabled_description")
    return f"{state}. {description}"


def _menu_markup(
    lm: LanguageManager,
    configuration: SecretaryConfiguration,
    session: ConfigurationContext,
    lang: Language,
    show_back_button: bool,
) -> InlineKeyboardMarkup:
    """Offers the behaviors not in effect, the content scanning toggle, and optionally a way back
    to the /start screen."""
    behavior_buttons = [
        _action_button(
            session, f"set_behavior:{behavior.name}", _behavior_display_name(lm, lang, behavior)
        )
        for behavior in (ScanningBehavior.PASSIVE, ScanningBehavior.STRICT)
        if behavior != configuration.behavior
    ]
    rows = message_helper.rows_of_two(behavior_buttons)
    if configuration.scanning_enabled:
        toggle = _action_button(
            session, "set_scanning:off", lm.get(lang, "secretary_settings", "disable_scanning")
        )
    else:
        toggle = _action_button(
            session, "set_scanning:on", lm.get(lang, "secretary_settings", "enable_scanning")
        )
    rows.append([toggle])
    if show_back_button:
        rows.append([_action_button(session, "start", lm.get(lang, "general", "back"))])
    return message_helper.markup(*rows)


def _action_button(session: ConfigurationContext, action: str, label: str) -> InlineKeyboardButton:
    return message_helper.button(label, f"{CALLBACK_PREFIX}:{session.hash}:{action}")


def _behavior_display_name(lm: LanguageManager, lang: Language, behavior: ScanningBehavior) -> str:
    return lm.get(lang, "secretary_settings", f"behavior_{behavior.name.lower()}")


def _set_scanning_enabled(context: HandlerContext, user_id: int, scanning_enabled: bool) -> None:
    try:
        context.managers.secretary_configurations.set_scanning_enabled(user_id, scanning_enabled)
    except DatabaseException as e:
        log.warning("Failed to save secretary scanning state for user %s: %s", user_id, e)


def _set_behavior(context: HandlerContext, user_id: int, behavior: ScanningBehavior) -> None:
    try:
        context.managers.secretary_configurations.set_behavior(user_id, behavior)
    except DatabaseException as e:
        log.warning("Failed to save secretary behavior for user %s: %s", user_id, e)
