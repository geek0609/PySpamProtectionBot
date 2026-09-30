"""Turns secretary mode on and off as Telegram Business connections come and go."""

import logging

from pyrogram.enums import ParseMode
from pyrogram.types import BusinessConnection, InlineKeyboardMarkup, User

from spb.classes.handler import Handler
from spb.classes.language_manager import LanguageManager
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.exceptions import DatabaseException
from spb.handlers.secretary import secretary_message_handler, secretary_settings_handler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.language import Language
from spb.utilities import message_helper

log = logging.getLogger(__name__)


@update_handler(UpdateType.BUSINESS_CONNECTION)
class SecretaryConnectionHandler(Handler):
    """Tracks Telegram Business connections for secretary mode.

    When a user connects or disconnects the bot as their manager, Telegram delivers a
    business_connection update. Connecting binds the user's secretary configuration to the
    connection, creating it with the defaults when needed, and explains in the user's private chat
    how secretary mode works; disconnecting removes the configuration and the connection's contacts
    and confirms the mode has been switched off.

    Secretary mode requires can_delete_all_messages; Telegram always provides read access. On
    connect the granted rights are compared against this and any missing permission is listed.
    """

    async def handle(self, context: HandlerContext) -> None:
        connection = context.current_update.business_connection
        assert connection is not None
        user = connection.user
        secretary_message_handler.track_business_owner(context, user)
        lm = context.languages
        lang = context.managers.language_preferences.get_user_language(user.id)

        if connection.is_enabled:
            _apply_enable(context, user.id, connection.id)
            html = _enabled_html(lm, lang, _missing_permissions(connection))
            await _send_secretary_message(context, user, html, _secretary_settings_markup(lm, lang))
        else:
            apply_disable(context, user.id, connection.id)
            await _send_secretary_message(context, user, _disabled_html(lm, lang), None)


def _apply_enable(context: HandlerContext, user_id: int, connection_id: str) -> None:
    """Binds the user's secretary configuration, created with the defaults when missing, to the
    connection so its business messages can be routed back to the user."""
    try:
        context.managers.secretary_configurations.set_business_connection_id(user_id, connection_id)
    except DatabaseException as e:
        log.warning("Failed to store secretary configuration for user %s: %s", user_id, e)


def apply_disable(context: HandlerContext, user_id: int, update_connection_id: str) -> None:
    """Removes all secretary data of the user: the configuration, every tracked contact of the
    user's business connection, and the in-memory contact claims of that connection. Global data
    such as the users table is left untouched.

    A claim marks a contact's first message as already being analyzed. Left behind after the
    contacts are deleted, it would make every message from those contacts be skipped unanalyzed if
    the owner reconnects before the claim expires.

    Contacts are only ever deleted for one connection: the stored one, or failing that the one the
    update is about. There is no "delete everything" fallback, since a repeated disable update
    arriving after the configuration is already gone would otherwise wipe every user's lists.
    """
    configurations = context.managers.secretary_configurations
    connection_id = None
    try:
        configuration = configurations.get_secretary_configuration(user_id)
        if configuration is not None:
            connection_id = configuration.business_connection_id
            configurations.delete_secretary_configuration(user_id)
    except DatabaseException as e:
        log.warning("Failed to remove secretary configuration for user %s: %s", user_id, e)

    connection_id = connection_id or update_connection_id
    try:
        context.managers.secretary_contacts.delete_secretary_contacts(connection_id)
    except DatabaseException as e:
        log.warning("Failed to remove secretary contacts for user %s: %s", user_id, e)

    claim_prefix = f"{secretary_message_handler.CONTACT_CLAIM_CACHE_PREFIX}{connection_id}:"
    context.cache.remove_if(lambda key: key.startswith(claim_prefix))


def _missing_permissions(connection: BusinessConnection) -> list[str]:
    """Returns the translation keys of the required permissions the business account has not
    granted. Telegram always grants secretary read access, so only deleting is checked."""
    rights = connection.rights
    if rights is None or not rights.can_delete_all_messages:
        return ["permission_can_delete_all_messages"]
    return []


def _enabled_html(lm: LanguageManager, lang: Language, missing: list[str]) -> str:
    """The message shown when secretary mode is enabled, listing any missing permission before the
    explanation."""
    html = [lm.get(lang, "secretary", "enabled_title")]
    if missing:
        html.append(lm.get(lang, "secretary", "missing_permissions_title"))
        html.append(lm.get(lang, "secretary", "missing_permissions_body") + "\n")
        for key in missing:
            permission = lm.get(lang, "secretary", key)
            html.append(lm.get(lang, "secretary", "permission_item", permission))
        html.append("\n" + lm.get(lang, "secretary", "missing_permissions_hint") + "\n\n")
    html.append(lm.get(lang, "secretary", "enabled_body"))
    return "".join(html)


def _disabled_html(lm: LanguageManager, lang: Language) -> str:
    """The message shown when secretary mode is disabled."""
    return lm.get(lang, "secretary", "disabled_title") + lm.get(lang, "secretary", "disabled_body")


def _secretary_settings_markup(lm: LanguageManager, lang: Language) -> InlineKeyboardMarkup:
    label = lm.get(lang, "secretary", "settings_button")
    return message_helper.single_row_markup(
        message_helper.button(label, secretary_settings_handler.OPEN_CALLBACK)
    )


async def _send_secretary_message(
    context: HandlerContext, user: User, html: str, markup: InlineKeyboardMarkup | None
) -> None:
    await Handler.try_execute(
        "secretary-connection",
        context.telegram_client.send_message,
        chat_id=user.id,
        text=html,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )
