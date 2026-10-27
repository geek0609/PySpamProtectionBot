"""The configuration menu of a group, opened from /start or /settings."""

import logging
import secrets
from collections.abc import Callable

from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import MessageNotModified, RPCError
from pyrogram.types import (
    ChatMember,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from spb.classes.handler import Handler
from spb.classes.language_manager import LanguageManager
from spb.classes.managers.chat_configuration_manager import ChatConfigurationManager
from spb.classes.update_handler import update_handler
from spb.enums import ConfigurationPage, JoinProtectionBehavior, ScanningBehavior, UpdateType
from spb.exceptions import DatabaseException
from spb.objects.context.configuration_context import ConfigurationContext
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.chat_configuration import ChatConfiguration
from spb.objects.language import Language
from spb.utilities import html_escape, message_helper

log = logging.getLogger(__name__)

CALLBACK_PREFIX = "cfg"

type _Row = list[InlineKeyboardButton]


@update_handler(UpdateType.CALLBACK_QUERY, callback_data=(f"{CALLBACK_PREFIX}:",))
class ConfigurationHandler(Handler):
    """Handles the buttons of the configuration menu opened from the /start command.

    Every button carries the configuration session hash and an action (for example enable,
    configure, or set_scanning:MODERATE). The handler validates that the press came from the
    session owner, applies the action to the chat configuration, and edits the message to show the
    resulting page.

    All page rendering, navigation, and state changes for the configuration menu live here so that
    future settings pages can be added without touching other handlers.
    """

    async def handle(self, context: HandlerContext) -> None:
        callback_query = context.current_update.callback_query
        assert callback_query is not None
        parts = callback_query.data.split(":", 2)
        if len(parts) != 3:
            await self.answer(context, callback_query)
            return

        _, hash, action = parts
        lm = context.languages
        session = context.sessions.configuration.find(hash)
        if session is None:
            lang = self.resolve_language(context, callback_query)
            await self.answer_alert(
                context, callback_query, lm.get(lang, "configuration", "session_expired")
            )
            await self.edit_to_expired(context, callback_query)
            return
        if session.user_id != callback_query.from_user.id:
            # Someone else's live menu: tell the presser, and leave the menu intact for its owner.
            lang = self.resolve_language(context, callback_query)
            await self.answer_alert(
                context, callback_query, lm.get(lang, "configuration", "session_not_owner")
            )
            return

        message = callback_query.message
        if message is None:
            await self.answer(context, callback_query)
            return

        # Checked live on every change rather than once when the menu opened, so an administrator
        # who loses the permission cannot keep using a menu they still have on screen.
        session_lang = self.session_language(context, session.user_id)
        if action in ("enable", "disable"):
            if not is_chat_owner(await _fetch_member(context, session)):
                owner_only = lm.get(session_lang, "configuration", "owner_only_alert")
                await self.answer_alert(context, callback_query, owner_only)
                return
        elif not _is_navigation_action(action) and not can_change_info(
            await _fetch_member(context, session)
        ):
            change_info_only = lm.get(session_lang, "configuration", "change_info_only_alert")
            await self.answer_alert(context, callback_query, change_info_only)
            return

        next_page = _apply_action(context, session, action)
        updated = context.sessions.configuration.update_page(hash, next_page)
        if updated is None:
            lang = self.resolve_language(context, callback_query)
            await self.answer_alert(
                context, callback_query, lm.get(lang, "configuration", "session_expired")
            )
            await self.edit_to_expired(context, callback_query)
            return

        configuration = context.managers.chat_configurations.resolve(updated.chat_id)
        await _edit_to_page(context, message, updated, configuration, next_page)
        await self.answer(context, callback_query, _action_toast(context, updated, action))


async def open_main_menu(context: HandlerContext, session: ConfigurationContext) -> None:
    """Sends the main configuration menu.

    When the session is ephemeral, the menu is sent to the group chat as an ephemeral message only
    the session owner sees. Otherwise it is sent to the user's private chat.
    """
    configuration = context.managers.chat_configurations.resolve(session.chat_id)
    lang = Handler.session_language(context, session.user_id)
    html = await _build_main_menu_html(context, configuration, lang)
    markup = _build_main_menu_markup(context, configuration, session, lang)

    source = context.current_update.message
    topic_id = (
        message_helper.topic_id(source)
        if source is not None and source.chat.id == session.chat_id
        else None
    )
    sent = await Handler.execute(
        "open-main-menu",
        context.telegram_client.send_message,
        chat_id=session.chat_id if session.ephemeral else session.user_id,
        text=html,
        parse_mode=ParseMode.HTML,
        message_thread_id=topic_id,
        ephemeral_message_parameters=message_helper.ephemeral_to(
            session.user_id if session.ephemeral else None
        ),
        reply_markup=markup,
    )

    sessions = context.sessions.configuration
    if session.ephemeral:
        sessions.update_ephemeral_message_id(session.hash, sent.ephemeral_message_id)
    else:
        sessions.update_message_id(session.hash, sent.id)


async def notify_channel_linked(context: HandlerContext, session: ConfigurationContext) -> None:
    """Updates the configuration menu to show that a notification chat has been linked.

    Called from the channel connect handler after a /connect <id> command succeeds.
    """
    configuration = context.managers.chat_configurations.resolve(session.chat_id)
    lang = Handler.session_language(context, session.user_id)
    markup = _build_channel_markup(context, configuration, session, lang)
    html = _build_channel_html(context, configuration, lang)

    client = context.telegram_client
    if session.ephemeral and session.ephemeral_message_id is not None:
        await Handler.execute(
            "notify-channel-linked",
            client.edit_ephemeral_message_text,
            chat_id=session.chat_id,
            receiver_user_id=session.user_id,
            ephemeral_message_id=session.ephemeral_message_id,
            text=html,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
        return

    await Handler.execute(
        "notify-channel-linked",
        client.edit_message_text,
        chat_id=session.user_id,
        message_id=session.message_id,
        text=html,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


def can_change_info(member: ChatMember | None) -> bool:
    """Whether a chat member may change the chat's settings: the owner, or an administrator who can
    change the chat's information. An administrator of a basic group holds every right."""
    if member is None:
        return False
    if member.status is ChatMemberStatus.OWNER:
        return True
    return member.status is ChatMemberStatus.ADMINISTRATOR and (
        member.privileges is None or member.privileges.can_change_info is True
    )


def is_chat_owner(member: ChatMember | None) -> bool:
    """Whether a chat member is the chat's owner."""
    return member is not None and member.status is ChatMemberStatus.OWNER


def refresh_channel_link_verification_code(
    context: HandlerContext, configuration: ChatConfiguration
) -> ChatConfiguration:
    """Generates a fresh channel link verification code, invalidating any previous one, and returns
    the updated configuration. Called each time the channel page is rendered while no channel is
    linked."""
    if configuration.channel_link_id is not None:
        return configuration

    chat_id = configuration.chat_id
    verification_code = 1_000_000_000 + secrets.randbelow(9_000_000_000)
    _update_configuration(
        context,
        chat_id,
        lambda manager: manager.set_channel_link_verification_code(chat_id, verification_code),
    )
    return context.managers.chat_configurations.resolve(chat_id)


def _apply_action(
    context: HandlerContext, session: ConfigurationContext, action: str
) -> ConfigurationPage:
    """Applies the requested action to the chat configuration and returns the page to show next."""
    chat_id = session.chat_id
    configuration = context.managers.chat_configurations.resolve(chat_id)
    authenticated = context.federation.is_authenticated
    page = session.page

    def update(write: Callable[[ChatConfigurationManager], None]) -> None:
        _update_configuration(context, chat_id, write)

    match action:
        case "enable":
            update(lambda manager: manager.enable_chat_configuration(chat_id))
            page = ConfigurationPage.MAIN
        case "disable":
            update(lambda manager: manager.delete_chat_configuration(chat_id))
            page = ConfigurationPage.MAIN
        case "configure":
            page = ConfigurationPage.SCANNING
        case "language":
            page = ConfigurationPage.LANGUAGE
        case "prev":
            page = _adjacent_visible_page(context, page, False)
        case "next":
            page = _adjacent_visible_page(context, page, True)
        case "menu":
            page = ConfigurationPage.MAIN
        case "toggle_scanning":
            enabled = not configuration.scanning_enabled
            update(lambda manager: manager.set_scanning_enabled(chat_id, enabled))
            page = ConfigurationPage.SCANNING
        case "toggle_ads_trackers":
            enabled = not configuration.block_ads_trackers
            update(lambda manager: manager.set_block_ads_trackers(chat_id, enabled))
            page = ConfigurationPage.SCANNING
        case "toggle_join_protection":
            enabled = not configuration.join_protection_enabled
            update(lambda manager: manager.set_join_protection_enabled(chat_id, enabled))
            page = ConfigurationPage.JOIN_PROTECTION
        case "toggle_join_protection_notifications":
            enabled = not configuration.join_protection_notifications_enabled
            update(
                lambda manager: manager.set_join_protection_notifications_enabled(chat_id, enabled)
            )
            _join_protection_passive_fallback(context, chat_id)
            page = ConfigurationPage.JOIN_PROTECTION
        case "toggle_privacy":
            enabled = not configuration.privacy_mode
            update(lambda manager: manager.set_privacy_mode(chat_id, enabled))
            page = ConfigurationPage.PRIVACY
        case "toggle_reporting" if authenticated:
            enabled = not configuration.reporting_enabled
            update(lambda manager: manager.set_reporting_enabled(chat_id, enabled))
            page = ConfigurationPage.REPORTING
        case "toggle_reporting" | "toggle_reporting_notifications" if not authenticated:
            # The button that reaches this is only ever rendered while authenticated; a stale one
            # surviving a restart lands back on the main menu instead of a page that is not
            # supposed to be reachable.
            page = ConfigurationPage.MAIN
        case "toggle_scanning_notifications":
            enabled = not configuration.scanning_notifications_enabled

            def toggle(manager: ChatConfigurationManager) -> None:
                manager.set_scanning_notifications_enabled(chat_id, enabled)
                if not enabled and configuration.scanning_behavior is ScanningBehavior.PASSIVE:
                    manager.set_scanning_behavior(chat_id, ScanningBehavior.MODERATE)

            update(toggle)
            page = ConfigurationPage.SCANNING
        case "toggle_reporting_notifications":
            enabled = not configuration.reporting_notifications_enabled
            update(lambda manager: manager.set_reporting_notifications_enabled(chat_id, enabled))
            page = ConfigurationPage.REPORTING
        case "disable_channel":
            update(lambda manager: manager.unlink_channel(chat_id))
            _join_protection_passive_fallback(context, chat_id)
            _scanning_passive_fallback(context, chat_id)
            page = ConfigurationPage.CHANNEL
        case "toggle_moderator_notifications":
            enabled = not configuration.moderator_notifications_enabled
            update(lambda manager: manager.set_moderator_notifications_enabled(chat_id, enabled))
            _join_protection_passive_fallback(context, chat_id)
            _scanning_passive_fallback(context, chat_id)
            page = ConfigurationPage.MODERATOR_NOTIFICATIONS
        case _ if action.startswith("set_scanning:"):
            requested = ScanningBehavior.from_string(action.removeprefix("set_scanning:"))
            scanning = (
                ScanningBehavior.MODERATE
                if requested is ScanningBehavior.PASSIVE
                and (
                    not _notification_avenue_available(configuration)
                    or not configuration.scanning_notifications_enabled
                )
                else requested
            )
            update(lambda manager: manager.set_scanning_behavior(chat_id, scanning))
            page = ConfigurationPage.SCANNING
        case _ if action.startswith("set_join_protection:"):
            requested_join = JoinProtectionBehavior.from_string(
                action.removeprefix("set_join_protection:")
            )
            join_protection = (
                JoinProtectionBehavior.MODERATE
                if requested_join is JoinProtectionBehavior.PASSIVE
                and (
                    not _notification_avenue_available(configuration)
                    or not configuration.join_protection_notifications_enabled
                )
                else requested_join
            )
            update(lambda manager: manager.set_join_protection_behavior(chat_id, join_protection))
            page = ConfigurationPage.JOIN_PROTECTION
        case _ if action.startswith("set_language:"):
            # A button from an older menu may name a language that is no longer shipped.
            language = context.languages.resolve(action.removeprefix("set_language:"))
            if language is not None:
                _save_chat_language(context, chat_id, language)
            page = ConfigurationPage.LANGUAGE
        case _:
            log.warning("Unknown configuration action '%s'", action)

    if session.page is ConfigurationPage.CHANNEL and page is not ConfigurationPage.CHANNEL:
        update(lambda manager: manager.set_channel_link_verification_code(chat_id, None))

    return page


def _adjacent_visible_page(
    context: HandlerContext, start: ConfigurationPage, forward: bool
) -> ConfigurationPage:
    """Steps to the next or previous page in carousel order, skipping any page that is not
    currently reachable rather than landing on it."""
    page = start.next() if forward else start.previous()
    for _ in ConfigurationPage:
        if _is_page_visible(context, page):
            break
        page = page.next() if forward else page.previous()
    return page


def _is_page_visible(context: HandlerContext, page: ConfigurationPage) -> bool:
    """Whether a settings page makes sense to show right now, rather than always existing but
    explaining why it will not work.

    Reporting is the one page whose entire feature, the bot submitting a report under its own
    identity, the Federation server refuses outright without the bot's own client permissions, so
    there is nothing to configure until then.
    """
    return page is not ConfigurationPage.REPORTING or context.federation.is_authenticated


async def _edit_to_page(
    context: HandlerContext,
    message: Message,
    session: ConfigurationContext,
    configuration: ChatConfiguration,
    page: ConfigurationPage,
) -> None:
    """Edits the menu message to show the given page."""
    lang = Handler.session_language(context, session.user_id)
    if page is ConfigurationPage.CHANNEL:
        configuration = refresh_channel_link_verification_code(context, configuration)
        context.sessions.configuration.update_channel_link_verification_code(
            session.hash, configuration.channel_link_verification_code
        )

    html = await _build_page_html(context, configuration, page, lang)
    markup = _build_page_markup(context, configuration, session, page, lang)

    callback_query = context.current_update.callback_query
    assert callback_query is not None
    try:
        await Handler.edit_session_message(context, message, callback_query, session, html, markup)
    except MessageNotModified:
        log.debug(
            "Configuration message %s in chat %s was already current", message.id, message.chat.id
        )
    except RPCError as e:
        message_helper.log_failed("edit-to-page", html, e)
        raise


def _update_configuration(
    context: HandlerContext, chat_id: int, write: Callable[[ChatConfigurationManager], None]
) -> None:
    """Applies a write to a chat's configuration, logging rather than raising when it fails."""
    try:
        write(context.managers.chat_configurations)
    except DatabaseException as e:
        log.warning("Failed to update chat configuration for chat %s: %s", chat_id, e)


def _join_protection_passive_fallback(context: HandlerContext, chat_id: int) -> None:
    """Moves Passive join protection to Moderate once its notifications can no longer reach
    anyone, so the chat is not left watching without anybody being told."""
    configuration = context.managers.chat_configurations.resolve(chat_id)
    if configuration.join_protection_behavior is JoinProtectionBehavior.PASSIVE and not (
        _notification_avenue_available(configuration)
        and configuration.join_protection_notifications_enabled
    ):
        _update_configuration(
            context,
            chat_id,
            lambda manager: manager.set_join_protection_behavior(
                chat_id, JoinProtectionBehavior.MODERATE
            ),
        )


def _scanning_passive_fallback(context: HandlerContext, chat_id: int) -> None:
    """Moves Passive scanning to Moderate once its notifications can no longer reach anyone."""
    configuration = context.managers.chat_configurations.resolve(chat_id)
    if configuration.scanning_behavior is ScanningBehavior.PASSIVE and not (
        _notification_avenue_available(configuration)
        and configuration.scanning_notifications_enabled
    ):
        _update_configuration(
            context,
            chat_id,
            lambda manager: manager.set_scanning_behavior(chat_id, ScanningBehavior.MODERATE),
        )


def _action_toast(
    context: HandlerContext, session: ConfigurationContext, action: str
) -> str | None:
    """The toast confirming a completed action, or None for pure navigation."""
    if _is_navigation_action(action):
        return None
    lang = Handler.session_language(context, session.user_id)
    lm = context.languages
    if action == "enable":
        return lm.get(lang, "configuration", "toast_enabled")
    if action == "disable":
        return lm.get(lang, "configuration", "toast_disabled")
    if action.startswith("set_language:"):
        return lm.get(lang, "configuration", "toast_language_changed")
    return lm.get(lang, "configuration", "toast_settings_updated")


def _is_navigation_action(action: str) -> bool:
    return action in ("prev", "next", "menu", "configure", "language")


async def _fetch_member(
    context: HandlerContext, session: ConfigurationContext
) -> ChatMember | None:
    """Fetches the session owner's current membership in the session's chat from Telegram, so
    permissions are checked against Telegram rather than the cached administrator list."""
    try:
        return await context.telegram_client.get_chat_member(session.chat_id, session.user_id)
    except RPCError as e:
        log.warning(
            "Unable to verify permissions of %s in chat %s: %s",
            session.user_id,
            session.chat_id,
            e,
        )
        return None


async def _build_main_menu_html(
    context: HandlerContext, configuration: ChatConfiguration, lang: Language
) -> str:
    lm = context.languages
    chat_info = context.chat_info.get_if_present(configuration.chat_id)
    chat_name = html_escape.escape(
        chat_info.name if chat_info is not None else lm.get(lang, "configuration", "this_chat")
    )
    if not configuration.enabled:
        return lm.get(lang, "configuration", "main_menu.disabled_header", chat_name)

    html = [lm.get(lang, "configuration", "main_menu.enabled_header", chat_name)]

    def line(key: str, *args: object) -> None:
        html.append(lm.get(lang, "configuration", key, *args) + "\n")

    if configuration.scanning_enabled:
        line(
            "main_menu.scanning_with_level",
            _scanning_display_name(lm, lang, configuration.scanning_behavior),
        )
    else:
        line("main_menu.scanning", _status(lm, lang, False))
    line(
        "main_menu.scanning_notifications",
        _status(lm, lang, configuration.scanning_notifications_enabled),
    )
    if configuration.join_protection_enabled:
        line(
            "main_menu.join_protection_with_level",
            _join_protection_display_name(lm, lang, configuration.join_protection_behavior),
        )
    else:
        line("main_menu.join_protection", _status(lm, lang, False))
    line(
        "main_menu.join_protection_notifications",
        _status(lm, lang, configuration.join_protection_notifications_enabled),
    )
    line("main_menu.reporting", _status(lm, lang, configuration.reporting_enabled))
    line(
        "main_menu.report_notifications",
        _status(lm, lang, configuration.reporting_notifications_enabled),
    )
    line("main_menu.privacy_mode", _status(lm, lang, configuration.privacy_mode))
    line(
        "main_menu.moderator_notifications",
        _status(lm, lang, configuration.moderator_notifications_enabled),
    )
    if configuration.channel_link_id is not None:
        line("main_menu.chat_linking", configuration.channel_link_id)
    else:
        line("main_menu.chat_linking_not_linked")

    server_information = await message_helper.server_information(context)
    if server_information is not None:
        html.append(lm.get(lang, "configuration", "main_menu.federation_server_header"))
        line("main_menu.federation_name", server_information.name)
        line("main_menu.federation_host", message_helper.server_host(context))
        line("main_menu.federation_api_version", server_information.api_version)
        access = (
            "main_menu.federation_access_authenticated"
            if context.federation.is_authenticated
            else "main_menu.federation_access_anonymous"
        )
        line("main_menu.federation_access", lm.get(lang, "configuration", access))

    return "".join(html)


def _build_main_menu_markup(
    context: HandlerContext,
    configuration: ChatConfiguration,
    session: ConfigurationContext,
    lang: Language,
) -> InlineKeyboardMarkup:
    lm = context.languages
    rows = [
        [
            _toggle_button(
                session,
                configuration.enabled,
                "enable",
                lm.get(lang, "buttons", "enable_bot"),
                "disable",
                lm.get(lang, "buttons", "disable_bot"),
            )
        ]
    ]
    if configuration.enabled:
        rows.append(
            [
                _action_button(session, "configure", lm.get(lang, "buttons", "configure_settings")),
                _action_button(session, "language", lm.get(lang, "buttons", "set_language")),
            ]
        )
    return InlineKeyboardMarkup(rows)


async def _build_page_html(
    context: HandlerContext,
    configuration: ChatConfiguration,
    page: ConfigurationPage,
    lang: Language,
) -> str:
    match page:
        case ConfigurationPage.MAIN:
            return await _build_main_menu_html(context, configuration, lang)
        case ConfigurationPage.SCANNING:
            return _build_scanning_html(context, configuration, lang)
        case ConfigurationPage.JOIN_PROTECTION:
            return _build_join_protection_html(context, configuration, lang)
        case ConfigurationPage.PRIVACY:
            return _build_privacy_html(context, configuration, lang)
        case ConfigurationPage.REPORTING:
            return _build_reporting_html(context, configuration, lang)
        case ConfigurationPage.MODERATOR_NOTIFICATIONS:
            return _build_moderator_notifications_html(context, configuration, lang)
        case ConfigurationPage.CHANNEL:
            return _build_channel_html(context, configuration, lang)
        case ConfigurationPage.LANGUAGE:
            return _build_language_html(context, configuration, lang)


def _build_page_markup(
    context: HandlerContext,
    configuration: ChatConfiguration,
    session: ConfigurationContext,
    page: ConfigurationPage,
    lang: Language,
) -> InlineKeyboardMarkup:
    match page:
        case ConfigurationPage.MAIN:
            return _build_main_menu_markup(context, configuration, session, lang)
        case ConfigurationPage.SCANNING:
            return _build_scanning_markup(context, configuration, session, lang)
        case ConfigurationPage.JOIN_PROTECTION:
            return _build_join_protection_markup(context, configuration, session, lang)
        case ConfigurationPage.PRIVACY:
            return _build_toggle_page_markup(
                context, session, lang, configuration.privacy_mode, "toggle_privacy"
            )
        case ConfigurationPage.REPORTING:
            return _build_reporting_markup(context, configuration, session, lang)
        case ConfigurationPage.MODERATOR_NOTIFICATIONS:
            return _build_toggle_page_markup(
                context,
                session,
                lang,
                configuration.moderator_notifications_enabled,
                "toggle_moderator_notifications",
            )
        case ConfigurationPage.CHANNEL:
            return _build_channel_markup(context, configuration, session, lang)
        case ConfigurationPage.LANGUAGE:
            return _build_language_page_markup(context, session, lang)


def _build_scanning_html(
    context: HandlerContext, configuration: ChatConfiguration, lang: Language
) -> str:
    lm = context.languages
    html = [lm.get(lang, "configuration", "scanning.title")]
    html.append(lm.get(lang, "configuration", "scanning.description"))
    if configuration.privacy_mode:
        note = lm.get(lang, "configuration", "scanning.privacy_mode_note")
        html.append(_privacy_mode_note(lm, lang, note))
    status = _status(lm, lang, configuration.scanning_enabled)
    html.append(lm.get(lang, "configuration", "scanning.status", status) + "\n\n")
    if configuration.scanning_enabled:
        block_ads_trackers = _status(lm, lang, configuration.block_ads_trackers)
        html.append(
            lm.get(lang, "configuration", "scanning.block_ads_trackers", block_ads_trackers)
            + "\n\n"
        )
        html.append(
            lm.get(lang, "configuration", "scanning.block_ads_trackers_description") + "\n\n"
        )
        level = _scanning_level(lm, lang, configuration.scanning_behavior)
        html.append(lm.get(lang, "configuration", "scanning.level", level) + "\n\n")
        if _notification_avenue_available(configuration):
            notifications = _status(lm, lang, configuration.scanning_notifications_enabled)
            html.append(lm.get(lang, "configuration", "scanning.notifications", notifications))
        else:
            html.append(lm.get(lang, "configuration", "scanning.notifications_unavailable"))
        html.append("\n\n")
    return "".join(html)


def _build_scanning_markup(
    context: HandlerContext,
    configuration: ChatConfiguration,
    session: ConfigurationContext,
    lang: Language,
) -> InlineKeyboardMarkup:
    lm = context.languages
    rows = [
        [
            _toggle_button(
                session,
                configuration.scanning_enabled,
                "toggle_scanning",
                lm.get(lang, "buttons", "enable"),
                "toggle_scanning",
                lm.get(lang, "buttons", "disable"),
            )
        ]
    ]
    if configuration.scanning_enabled:
        rows.append(
            [
                _toggle_button(
                    session,
                    configuration.block_ads_trackers,
                    "toggle_ads_trackers",
                    lm.get(lang, "configuration", "scanning.enable_ads_trackers"),
                    "toggle_ads_trackers",
                    lm.get(lang, "configuration", "scanning.disable_ads_trackers"),
                )
            ]
        )
        passive_available = (
            configuration.scanning_notifications_enabled
            and _notification_avenue_available(configuration)
        )
        behavior_buttons = [
            _action_button(
                session,
                f"set_scanning:{behavior.name}",
                _scanning_display_name(lm, lang, behavior),
            )
            for behavior in ScanningBehavior
            if behavior is not configuration.scanning_behavior
            and (behavior is not ScanningBehavior.PASSIVE or passive_available)
        ]
        rows += message_helper.rows_of_two(behavior_buttons)
        if _notification_avenue_available(configuration):
            rows.append(
                [
                    _toggle_button(
                        session,
                        configuration.scanning_notifications_enabled,
                        "toggle_scanning_notifications",
                        lm.get(lang, "buttons", "enable_notifications"),
                        "toggle_scanning_notifications",
                        lm.get(lang, "buttons", "disable_notifications"),
                    )
                ]
            )
    rows.append(_navigation_row(lm, lang, session))
    return InlineKeyboardMarkup(rows)


def _build_join_protection_html(
    context: HandlerContext, configuration: ChatConfiguration, lang: Language
) -> str:
    lm = context.languages
    html = [lm.get(lang, "configuration", "join_protection.title")]
    html.append(lm.get(lang, "configuration", "join_protection.description"))
    if configuration.privacy_mode:
        note = lm.get(lang, "configuration", "join_protection.privacy_mode_note")
        html.append(_privacy_mode_note(lm, lang, note))

    status = _status(lm, lang, configuration.join_protection_enabled)
    html.append(lm.get(lang, "configuration", "join_protection.status", status) + "\n\n")
    if configuration.join_protection_enabled:
        level = _join_protection_level(lm, lang, configuration.join_protection_behavior)
        html.append(lm.get(lang, "configuration", "join_protection.level", level) + "\n\n")
        if _notification_avenue_available(configuration):
            notifications = _status(lm, lang, configuration.join_protection_notifications_enabled)
            html.append(
                lm.get(lang, "configuration", "join_protection.notifications", notifications)
            )
        else:
            html.append(lm.get(lang, "configuration", "join_protection.notifications_unavailable"))
        html.append("\n\n")
    return "".join(html)


def _build_join_protection_markup(
    context: HandlerContext,
    configuration: ChatConfiguration,
    session: ConfigurationContext,
    lang: Language,
) -> InlineKeyboardMarkup:
    lm = context.languages
    rows = [
        [
            _toggle_button(
                session,
                configuration.join_protection_enabled,
                "toggle_join_protection",
                lm.get(lang, "buttons", "enable"),
                "toggle_join_protection",
                lm.get(lang, "buttons", "disable"),
            )
        ]
    ]
    if configuration.join_protection_enabled:
        passive_available = (
            _notification_avenue_available(configuration)
            and configuration.join_protection_notifications_enabled
        )
        behavior_buttons = [
            _action_button(
                session,
                f"set_join_protection:{behavior.name}",
                _join_protection_display_name(lm, lang, behavior),
            )
            for behavior in JoinProtectionBehavior
            if behavior is not configuration.join_protection_behavior
            and (behavior is not JoinProtectionBehavior.PASSIVE or passive_available)
        ]
        rows += message_helper.rows_of_two(behavior_buttons)
        if _notification_avenue_available(configuration):
            rows.append(
                [
                    _toggle_button(
                        session,
                        configuration.join_protection_notifications_enabled,
                        "toggle_join_protection_notifications",
                        lm.get(lang, "buttons", "enable_notifications"),
                        "toggle_join_protection_notifications",
                        lm.get(lang, "buttons", "disable_notifications"),
                    )
                ]
            )
    rows.append(_navigation_row(lm, lang, session))
    return InlineKeyboardMarkup(rows)


def _build_privacy_html(
    context: HandlerContext, configuration: ChatConfiguration, lang: Language
) -> str:
    lm = context.languages
    status = _status(lm, lang, configuration.privacy_mode)
    return (
        lm.get(lang, "configuration", "privacy.title")
        + lm.get(lang, "configuration", "privacy.description")
        + lm.get(lang, "configuration", "privacy.status", status)
        + "\n\n"
    )


def _build_toggle_page_markup(
    context: HandlerContext,
    session: ConfigurationContext,
    lang: Language,
    enabled: bool,
    toggle_action: str,
) -> InlineKeyboardMarkup:
    """Builds the keyboard of a page holding a single setting: its toggle and the navigation."""
    lm = context.languages
    toggle = _toggle_button(
        session,
        enabled,
        toggle_action,
        lm.get(lang, "buttons", "enable"),
        toggle_action,
        lm.get(lang, "buttons", "disable"),
    )
    return InlineKeyboardMarkup([[toggle], _navigation_row(lm, lang, session)])


def _build_reporting_html(
    context: HandlerContext, configuration: ChatConfiguration, lang: Language
) -> str:
    lm = context.languages
    html = [lm.get(lang, "configuration", "reporting.title")]
    html.append(lm.get(lang, "configuration", "reporting.description"))
    if configuration.privacy_mode:
        note = lm.get(lang, "configuration", "reporting.privacy_mode_note")
        html.append(_privacy_mode_note(lm, lang, note))
    status = _status(lm, lang, configuration.reporting_enabled)
    html.append(lm.get(lang, "configuration", "reporting.status", status) + "\n\n")
    if configuration.reporting_enabled:
        if _notification_avenue_available(configuration):
            notifications = _status(lm, lang, configuration.reporting_notifications_enabled)
            html.append(lm.get(lang, "configuration", "reporting.notifications", notifications))
        else:
            html.append(lm.get(lang, "configuration", "reporting.notifications_unavailable"))
        html.append("\n\n")
    return "".join(html)


def _build_reporting_markup(
    context: HandlerContext,
    configuration: ChatConfiguration,
    session: ConfigurationContext,
    lang: Language,
) -> InlineKeyboardMarkup:
    lm = context.languages
    rows = [
        [
            _toggle_button(
                session,
                configuration.reporting_enabled,
                "toggle_reporting",
                lm.get(lang, "buttons", "enable"),
                "toggle_reporting",
                lm.get(lang, "buttons", "disable"),
            )
        ]
    ]
    if configuration.reporting_enabled and _notification_avenue_available(configuration):
        rows.append(
            [
                _toggle_button(
                    session,
                    configuration.reporting_notifications_enabled,
                    "toggle_reporting_notifications",
                    lm.get(lang, "buttons", "enable_notifications"),
                    "toggle_reporting_notifications",
                    lm.get(lang, "buttons", "disable_notifications"),
                )
            ]
        )
    rows.append(_navigation_row(lm, lang, session))
    return InlineKeyboardMarkup(rows)


def _build_moderator_notifications_html(
    context: HandlerContext, configuration: ChatConfiguration, lang: Language
) -> str:
    lm = context.languages
    status = _status(lm, lang, configuration.moderator_notifications_enabled)
    return (
        lm.get(lang, "configuration", "moderator_notifications.title")
        + lm.get(lang, "configuration", "moderator_notifications.description")
        + lm.get(lang, "configuration", "moderator_notifications.status", status)
        + "\n\n"
    )


def _build_channel_html(
    context: HandlerContext, configuration: ChatConfiguration, lang: Language
) -> str:
    """Describes chat linking and either the linked chat or how to link one."""
    lm = context.languages
    html = [lm.get(lang, "configuration", "channel.title")]
    html.append(lm.get(lang, "configuration", "channel.description"))

    if configuration.channel_link_id is not None:
        html.append(
            lm.get(lang, "configuration", "channel.linked_chat", configuration.channel_link_id)
        )
    else:
        code = configuration.channel_link_verification_code
        html.append(lm.get(lang, "configuration", "channel.status_not_linked"))
        html.append(lm.get(lang, "configuration", "channel.post_command_instruction"))
        html.append(f"<code>/connect {code if code is not None else 'N/A'}</code>\n")

    return "".join(html)


def _build_channel_markup(
    context: HandlerContext,
    configuration: ChatConfiguration,
    session: ConfigurationContext,
    lang: Language,
) -> InlineKeyboardMarkup:
    lm = context.languages
    rows: list[_Row] = []
    if configuration.channel_link_id is not None:
        unlink = lm.get(lang, "buttons", "unlink_chat")
        rows.append([_action_button(session, "disable_channel", unlink)])
    rows.append(_navigation_row(lm, lang, session))
    return InlineKeyboardMarkup(rows)


def _build_language_html(
    context: HandlerContext, configuration: ChatConfiguration, lang: Language
) -> str:
    """Asks for a language, naming the chat's current one."""
    lm = context.languages
    current = context.managers.language_preferences.get_chat_language(configuration.chat_id)
    return (
        lm.get(lang, "general", "language.select")
        + "\n\n"
        + lm.get(lang, "general", "language.current", current.emoji, current.name)
    )


def _build_language_page_markup(
    context: HandlerContext, session: ConfigurationContext, lang: Language
) -> InlineKeyboardMarkup:
    """Offers every available language, four to a row, and a way back to the menu."""
    buttons = [
        _action_button(session, f"set_language:{language.code}", language.emoji)
        for language in context.languages.available_languages
    ]
    rows = [buttons[index : index + 4] for index in range(0, len(buttons), 4)]
    rows.append(
        [_action_button(session, "menu", context.languages.get(lang, "buttons", "nav_menu"))]
    )
    return InlineKeyboardMarkup(rows)


def _save_chat_language(context: HandlerContext, chat_id: int, language: Language) -> None:
    try:
        context.managers.language_preferences.set_chat_language(chat_id, language)
    except DatabaseException as e:
        log.warning("Failed to save chat language for chat %s: %s", chat_id, e)


def _privacy_mode_note(lm: LanguageManager, lang: Language, text: str) -> str:
    return lm.get(lang, "configuration", "privacy_mode_label") + text + "\n\n"


def _navigation_row(lm: LanguageManager, lang: Language, session: ConfigurationContext) -> _Row:
    return [
        _action_button(session, "prev", lm.get(lang, "buttons", "nav_prev")),
        _action_button(session, "menu", lm.get(lang, "buttons", "nav_menu")),
        _action_button(session, "next", lm.get(lang, "buttons", "nav_next")),
    ]


def _toggle_button(
    session: ConfigurationContext,
    current: bool,
    enable_action: str,
    enable_label: str,
    disable_action: str,
    disable_label: str,
) -> InlineKeyboardButton:
    """Builds the button that flips a setting, labelled with what pressing it will do."""
    if current:
        return _action_button(session, disable_action, disable_label)
    return _action_button(session, enable_action, enable_label)


def _action_button(session: ConfigurationContext, action: str, label: str) -> InlineKeyboardButton:
    return message_helper.button(label, f"{CALLBACK_PREFIX}:{session.hash}:{action}")


def _status(lm: LanguageManager, lang: Language, enabled: bool) -> str:
    return lm.get(lang, "general", "enabled" if enabled else "disabled")


def _notification_avenue_available(configuration: ChatConfiguration) -> bool:
    """Whether notifications can reach anyone: moderators privately, or a linked chat."""
    return (
        configuration.moderator_notifications_enabled or configuration.channel_link_id is not None
    )


def _scanning_level(lm: LanguageManager, lang: Language, behavior: ScanningBehavior) -> str:
    name = _scanning_display_name(lm, lang, behavior)
    description = lm.get(
        lang, "configuration", f"scanning.behavior_{behavior.name.lower()}_description"
    )
    return f"{name} \N{EM DASH} {description}"


def _join_protection_level(
    lm: LanguageManager, lang: Language, behavior: JoinProtectionBehavior
) -> str:
    name = _join_protection_display_name(lm, lang, behavior)
    description = lm.get(
        lang, "configuration", f"join_protection.behavior_{behavior.name.lower()}_description"
    )
    return f"{name} \N{EM DASH} {description}"


def _scanning_display_name(lm: LanguageManager, lang: Language, behavior: ScanningBehavior) -> str:
    return lm.get(lang, "configuration", f"scanning.behavior_{behavior.name.lower()}")


def _join_protection_display_name(
    lm: LanguageManager, lang: Language, behavior: JoinProtectionBehavior
) -> str:
    return lm.get(lang, "configuration", f"join_protection.behavior_{behavior.name.lower()}")
