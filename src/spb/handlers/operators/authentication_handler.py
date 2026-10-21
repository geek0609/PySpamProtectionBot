"""The /auth, /deauth and /authinfo commands."""

import logging

from pyfederation.records import OperatorRecord
from pyrogram.types import InlineKeyboardMarkup, Message

from spb.classes.federation_service import FederationService
from spb.classes.federation_web_links import FederationWebLinks, Record
from spb.classes.handler import Handler
from spb.classes.managers.operator_manager import OperatorManager
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.exceptions import DatabaseException, FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.operator_identity import OperatorIdentity
from spb.objects.language import Language
from spb.utilities import html_escape, message_helper, moderation_actions

log = logging.getLogger(__name__)


@update_handler(UpdateType.COMMAND, commands=("auth", "deauth", "authinfo"))
class AuthenticationHandler(Handler):
    """Links a Telegram user to a Federation operator.

    /auth <token> identifies the access token with the Federation server and stores it as the
    user's credential. It is only accepted in a private chat, since anywhere else the token would
    be visible to the whole chat. /deauth removes it, and /authinfo shows the operator it belongs
    to. Every lookup runs on a short-lived client bound to the user's own credential and returns
    the current operator record. The process-wide Federation client is never changed, so one
    user's command cannot authenticate another user's Federation request.
    """

    async def handle(self, context: HandlerContext) -> None:
        message = context.current_update.message
        if message is None or message.from_user is None:
            return

        lang = self.resolve_language(context, message)
        match context.command_name:
            case "auth":
                await self._authenticate(context, message, lang)
            case "deauth":
                await self._deauthenticate(context, message, lang)
            case _:
                await self._show_authentication_info(context, message, lang)

    async def _authenticate(
        self, context: HandlerContext, message: Message, lang: Language
    ) -> None:
        """Stores the access token given as the user's credential, once the server accepts it."""
        lm = context.languages
        if not message_helper.is_private(message.chat):
            if message_helper.get_message_payload(message) is None:
                private_only = lm.get(lang, "authentication", "private_only")
                await self.send_html(context, message, private_only)
            elif await moderation_actions.delete_message(context, message.chat.id, message.id):
                token_deleted = lm.get(lang, "authentication", "token_deleted")
                await self.send_html(context, message, token_deleted)
            else:
                token_not_deleted = lm.get(lang, "authentication", "token_not_deleted")
                await self.send_html(context, message, token_not_deleted)
            return

        if not context.federation.is_available:
            await self.send_html(context, message, lm.get(lang, "authentication", "unavailable"))
            return

        access_token = message_helper.single_argument(message)
        if access_token is None:
            await self.send_html(context, message, lm.get(lang, "authentication", "usage"))
            return

        user_id = message.from_user.id
        try:
            operator = await _store_credential(
                context.managers.operators, context.federation, access_token, user_id
            )
        except FederationException as e:
            log.info("Federation authentication rejected for Telegram user %s: %s", user_id, e)
            await self.send_html(context, message, lm.get(lang, "authentication", "rejected"))
            return
        except DatabaseException as e:
            log.warning(
                "Failed to persist operator authentication for Telegram user %s: %s", user_id, e
            )
            failed = lm.get(lang, "authentication", "credential_store_failed")
            await self.send_html(context, message, failed)
            return

        authenticated = lm.get(
            lang,
            "authentication",
            "authenticated",
            html_escape.escape(operator.name),
            html_escape.escape(operator.uuid),
        )
        markup = _operator_link_markup(context, lang, operator)
        await self.send_html(context, message, authenticated, markup=markup)

    async def _deauthenticate(
        self, context: HandlerContext, message: Message, lang: Language
    ) -> None:
        """Removes the user's stored operator credential."""
        lm = context.languages
        user_id = message.from_user.id
        try:
            context.managers.operators.delete_operator(user_id)
        except DatabaseException as e:
            log.warning(
                "Failed to remove operator authentication for Telegram user %s: %s", user_id, e
            )
            await self.send_html(context, message, lm.get(lang, "authentication", "signout_failed"))
            return
        await self.send_html(context, message, lm.get(lang, "authentication", "signed_out"))

    async def _show_authentication_info(
        self, context: HandlerContext, message: Message, lang: Language
    ) -> None:
        """Shows the operator the user's credential belongs to, as the server knows it now."""
        lm = context.languages
        if not context.federation.is_available:
            await self.send_html(context, message, lm.get(lang, "authentication", "unavailable"))
            return

        user_id = message.from_user.id
        identity = context.managers.operators.get_operator(user_id)
        if identity is None:
            not_authenticated = lm.get(lang, "authentication", "not_authenticated")
            await self.send_html(context, message, not_authenticated)
            return

        try:
            operator = await context.federation.operator_for(identity.access_token)
        except FederationException as e:
            log.info("Federation authentication lookup failed for Telegram user %s: %s", user_id, e)
            unavailable = lm.get(lang, "authentication", "authinfo_unavailable")
            await self.send_html(context, message, unavailable)
            return

        markup = _operator_link_markup(context, lang, operator)
        await self.send_html(
            context, message, _operator_info(context, lang, operator), markup=markup
        )


async def _store_credential(
    operators: OperatorManager, federation: FederationService, access_token: str, user_id: int
) -> OperatorRecord:
    """Identifies an access token with the Federation server and stores the credential.

    The credential is only written once the server has accepted the token, so a rejected token
    never leaves a stored credential that would fail on every later use.

    Raises:
        FederationException: If the server is unavailable or rejected the token.
        DatabaseException: If the credential cannot be stored.
    """
    operator = await federation.operator_for(access_token)
    operators.save_operator(user_id, OperatorIdentity(operator.uuid, access_token))
    return operator


def _operator_info(context: HandlerContext, lang: Language, operator: OperatorRecord) -> str:
    """Describes an operator: name, UUID, status, and permissions."""
    lm = context.languages

    def permission(granted: bool) -> str:
        return lm.get(lang, "general", "enabled" if granted else "disabled")

    status = "status_disabled" if operator.disabled else "status_active"
    return (
        lm.get(lang, "authentication", "operator_header")
        + lm.get(lang, "authentication", "name", html_escape.escape(operator.name))
        + "\n"
        + lm.get(lang, "authentication", "uuid", html_escape.escape(operator.uuid))
        + "\n"
        + lm.get(lang, "authentication", status)
        + "\n\n"
        + lm.get(lang, "authentication", "permissions_header")
        + lm.get(
            lang, "authentication", "permission_client", permission(operator.permissions.client)
        )
        + "\n"
        + lm.get(
            lang,
            "authentication",
            "permission_operator",
            permission(operator.permissions.operator),
        )
        + "\n"
        + lm.get(
            lang,
            "authentication",
            "permission_management",
            permission(operator.permissions.management),
        )
        + "\n"
        + lm.get(
            lang,
            "authentication",
            "permission_auto_assign",
            permission(operator.auto_assign_reports),
        )
        + "\n"
        + lm.get(
            lang,
            "authentication",
            "permission_auto_assign_automated",
            permission(operator.auto_assign_automated_reports),
        )
    )


def _operator_link_markup(
    context: HandlerContext, lang: Language, operator: OperatorRecord
) -> InlineKeyboardMarkup | None:
    """The keyboard linking the operator's page in the Federation Web Application, if any."""
    return FederationWebLinks.attach(
        None, context.web_links.button(context.languages, lang, Record.OPERATOR, operator.uuid)
    )
