"""The messages that arrive through a Telegram Business connection."""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pyfederation.enums import SuggestedAction
from pyrogram.enums import ParseMode
from pyrogram.errors import RPCError
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyParameters,
    User,
)

from spb.classes.federation_web_links import FederationWebLinks, Record
from spb.classes.handler import Handler
from spb.classes.language_manager import LanguageManager
from spb.classes.notifications import notification_formatter
from spb.classes.update_handler import update_handler
from spb.enums import ScanningBehavior, SecretaryContactStatus, UpdateType
from spb.exceptions import DatabaseException, FederationException
from spb.handlers.secretary import secretary_settings_handler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.secretary_configuration import SecretaryConfiguration
from spb.objects.database.secretary_contact import SecretaryContact
from spb.objects.database.user_identity import UserIdentity
from spb.objects.language import Language
from spb.objects.secretary_report_target import SecretaryReportTarget
from spb.utilities import flat_metadata, message_content, message_helper, report_attachments

log = logging.getLogger(__name__)

CONTACT_CLAIM_CACHE_PREFIX = "secretary-contact-claim:"
MAX_EVIDENCE_CAPTION_LENGTH = 1_024

_ACTION_RANK = {
    SuggestedAction.CAUTION: 1,
    SuggestedAction.BLOCK_CONTENT: 2,
    SuggestedAction.TEMPORARILY_BLOCK_ENTITY: 3,
    SuggestedAction.PERMANENTLY_BLOCK_ENTITY: 4,
}
"""How severe each suggested action is; the more severe of two suggestions wins."""


@dataclass(frozen=True, slots=True)
class AttachmentInfo:
    """The details of a message attachment, used either to send the file again by its id or to
    describe it when that is not possible."""

    file_id: str
    type_key: str
    file_name: str | None
    mime_type: str | None
    file_size: int | None


@dataclass(frozen=True, slots=True)
class Recommendation:
    """What Federation made of a message and its sender.

    Attributes:
        analyzed: Whether Federation analyzed the message or its sender at all.
        suggestion: The most severe suggested action, or None when there was no reason to act.
        entity_uuid: The sender's Federation entity, or None when it was not resolved.
    """

    analyzed: bool
    suggestion: SuggestedAction | None
    entity_uuid: str | None


UNKNOWN = Recommendation(False, None, None)
"""Federation was not asked, so nothing is known either way."""


@dataclass(frozen=True, slots=True)
class ContactDecision:
    """The owner's decision about one contact of a business connection.

    Attributes:
        connection_id: The business connection.
        contact_id: The contact.
        lang: The owner's language.
        contact_status: The contact's current status.
    """

    connection_id: str
    contact_id: int
    lang: Language
    contact_status: SecretaryContactStatus


@update_handler(
    UpdateType.BUSINESS_MESSAGE,
    UpdateType.EDITED_BUSINESS_MESSAGE,
    UpdateType.DELETED_BUSINESS_MESSAGES,
)
class SecretaryMessageHandler(Handler):
    """Handles the messages that arrive through a Telegram Business connection.

    This is the working half of secretary mode. A message from somebody the owner has not spoken to
    before is checked against Federation, the contact is allowed or denied according to the owner's
    chosen behavior, and the owner is notified with buttons to overturn the decision or to report
    the message to Federation, including one that was not flagged. A message from an
    already-allowed contact is left alone; one from a denied contact is deleted without another
    notification.

    Turning secretary mode on and off is SecretaryConnectionHandler's job, and the settings menu is
    SecretarySettingsHandler's.
    """

    async def handle(self, context: HandlerContext) -> None:
        update = context.current_update
        if update.business_message is not None:
            await _process_business_message(context, update.business_message)
        elif update.edited_business_message is not None:
            await _process_business_message(context, update.edited_business_message)
        elif update.deleted_business_messages:
            # Nothing is actionable once Telegram has already removed the messages.
            deleted = update.deleted_business_messages
            log.debug(
                "Business messages %s deleted in connection %s (chat %s)",
                [message.id for message in deleted],
                deleted[0].business_connection_id,
                deleted[0].chat.id,
            )


async def _process_business_message(context: HandlerContext, message: Message) -> None:
    """Decides what to do about a business message: an owner's /allow or /deny, a message from a
    contact already decided on, or a first message that Federation is asked about."""
    connection_id = message.business_connection_id
    secretaries = context.managers.secretary_configurations
    owner_id = secretaries.user_id_by_business_connection(connection_id)
    if owner_id is None:
        owner_id = secretaries.bind_unknown_connection(connection_id)
        if owner_id is None:
            log.debug(
                "Business message %s ignored: secretary mode is not enabled for connection %s",
                message.id,
                connection_id,
            )
            return
        log.info(
            "Business message %s enabled secretary mode: connection %s bound to user %s",
            message.id,
            connection_id,
            owner_id,
        )

    sender = message.from_user
    command = _decision_command(message)
    if command is not None and sender is not None and sender.id == owner_id:
        # In a business chat the chat is the private conversation with the contact, so its id is
        # the contact's user id.
        allowed = command == "/allow" or command.startswith("/allow@")
        status = SecretaryContactStatus.ALLOWED if allowed else SecretaryContactStatus.DENIED
        _set_contact_status(context, connection_id, message.chat.id, status)
        return

    if sender is None:
        log.debug("Business message %s ignored: no sender", message.id)
        return
    if sender.id == owner_id:
        log.debug("Business message %s from secretary owner %s is ignored", message.id, owner_id)
        return

    track_business_owner(context, sender)
    configuration = secretaries.resolve(owner_id)
    contact = context.managers.secretary_contacts.get_secretary_contact(connection_id, sender.id)

    if contact is not None and contact.status is SecretaryContactStatus.ALLOWED:
        log.debug(
            "Business message %s from approved contact %s in connection %s is ignored",
            message.id,
            sender.id,
            connection_id,
        )
        return

    if contact is not None and contact.status is SecretaryContactStatus.DENIED:
        await _delete_business_message(context, message)
        return

    # The contact is absent or still unknown, so it needs to be analyzed. Only the first task to
    # claim the contact may register, analyze, and decide for it; duplicates from a catch-up batch
    # are skipped so exactly one decision (and owner notification) is emitted.
    if not _claim_contact(context, connection_id, sender.id):
        log.debug(
            "Business message %s from %s in connection %s skipped: contact claim held elsewhere",
            message.id,
            sender.id,
            connection_id,
        )
        return

    if contact is None:
        log.info("Registered new secretary contact %s in connection %s", sender.id, connection_id)
        _register_secretary_contact(context, message, sender)

    recommendation = await recommend(context, configuration, message, sender)
    if not recommendation.analyzed:
        # No analysis was possible (Federation offline, textless message in privacy mode, ...),
        # so the contact stays unknown. Release the claim so a later message can retry it.
        _release_contact_claim(context, connection_id, sender.id)
        log.debug(
            "Business message %s from %s in connection %s passes through; contact stays unknown",
            message.id,
            sender.id,
            connection_id,
        )
        return

    suggestion = recommendation.suggestion
    if configuration.behavior is ScanningBehavior.STRICT:
        if suggestion is None:
            if not _set_contact_status(
                context, connection_id, sender.id, SecretaryContactStatus.ALLOWED
            ):
                _release_contact_claim(context, connection_id, sender.id)
                return
            await _notify_allowed_owner(context, owner_id, message, recommendation.entity_uuid)
        else:
            deleted = await _delete_business_message(context, message)
            if not _set_contact_status(
                context, connection_id, sender.id, SecretaryContactStatus.DENIED
            ):
                _release_contact_claim(context, connection_id, sender.id)
                return
            await _notify_owner(
                context,
                owner_id,
                message,
                suggestion,
                deleted,
                SecretaryContactStatus.DENIED,
                recommendation.entity_uuid,
            )
        return

    # Passive: automatically allow first-contact messages, notifying the owner of every decision so
    # they can review and change it. Flagged messages are kept in the chat; the owner is notified
    # with the Federation suggestion.
    if not _set_contact_status(context, connection_id, sender.id, SecretaryContactStatus.ALLOWED):
        _release_contact_claim(context, connection_id, sender.id)
        return
    if suggestion is None:
        await _notify_allowed_owner(context, owner_id, message, recommendation.entity_uuid)
    else:
        await _notify_owner(
            context,
            owner_id,
            message,
            suggestion,
            False,
            SecretaryContactStatus.ALLOWED,
            recommendation.entity_uuid,
        )


async def recommend(
    context: HandlerContext,
    configuration: SecretaryConfiguration,
    message: Message,
    sender: User,
) -> Recommendation:
    """Asks Federation about a business message and its sender.

    The content is only scanned when the owner enabled scanning; the sender is queried either way.

    Returns:
        The more severe of the two suggestions with the sender's entity, or UNKNOWN when Federation
        is unavailable or neither the content nor the sender could be analyzed.
    """
    federation = context.federation
    if not federation.is_available:
        return UNKNOWN

    text = message_content.text_or_caption(message) if configuration.scanning_enabled else None
    has_text = text is not None and bool(text.strip())
    author_entity = f"{sender.id}@telegram.org"
    content_analyzed = entity_analyzed = False
    content_suggestion = entity_suggestion = None
    entity_uuid = None

    if has_text:
        try:
            scanned = await federation.scan_content(
                message_content.build_scan_input(message, False), author_entity
            )
        except FederationException as e:
            log.warning(
                "Failed to scan business message %s from %s in connection %s: %s",
                message.id,
                sender.id,
                message.business_connection_id,
                e,
            )
        else:
            content_analyzed = True
            content_suggestion = scanned.suggested_action
            if scanned.author_entity is not None:
                entity_uuid = scanned.author_entity.entity.uuid

    if not has_text or configuration.behavior is ScanningBehavior.STRICT:
        try:
            query = await federation.query_entity(author_entity)
        except FederationException as e:
            log.warning(
                "Failed to query business sender %s in connection %s: %s",
                sender.id,
                message.business_connection_id,
                e,
            )
        else:
            if query is not None:
                entity_analyzed = True
                entity_suggestion = query.suggested_action
                entity_uuid = query.entity_record.uuid

    if not content_analyzed and not entity_analyzed:
        return UNKNOWN
    suggestions = [s for s in (content_suggestion, entity_suggestion) if s is not None]
    # On a tie the content suggestion, listed first, wins.
    suggestion = max(suggestions, key=_ACTION_RANK.__getitem__) if suggestions else None
    return Recommendation(True, suggestion, entity_uuid)


def track_business_owner(context: HandlerContext, user: User) -> None:
    """Records a user seen through a business connection."""
    try:
        context.managers.users.save_user(
            UserIdentity(user.id, user.username, user.first_name, user.last_name)
        )
    except DatabaseException as e:
        log.warning("Failed to track user %s: %s", user.id, e)


def contact_decision_markup(lm: LanguageManager, decision: ContactDecision) -> InlineKeyboardMarkup:
    """The buttons on an owner notification that overturn the decision about a contact."""
    return _contact_decision_markup(
        lm, decision, secretary_settings_handler.CONTACT_CALLBACK_PREFIX, False
    )


def contact_settings_decision_markup(
    lm: LanguageManager,
    connection_id: str,
    contact_id: int,
    lang: Language,
    contact_status: SecretaryContactStatus,
) -> InlineKeyboardMarkup:
    """The buttons on a contact's settings page, with a way to the secretary settings."""
    decision = ContactDecision(connection_id, contact_id, lang, contact_status)
    return _contact_decision_markup(
        lm, decision, secretary_settings_handler.CONTACT_SETTINGS_CALLBACK_PREFIX, True
    )


async def _delete_business_message(context: HandlerContext, message: Message) -> bool:
    """Deletes a message of a business chat on the owner's behalf.

    Returns:
        True when the message was deleted.
    """
    message_helper.log_outgoing(
        "secretary-delete",
        {"business_connection_id": message.business_connection_id, "message_ids": [message.id]},
    )
    try:
        await context.telegram_client.delete_business_messages(
            message.business_connection_id, [message.id]
        )
    except RPCError as e:
        log.warning(
            "Failed to delete business message %s in chat %s: %s", message.id, message.chat.id, e
        )
        return False
    return True


def _evidence_caption(lm: LanguageManager, lang: Language, message: Message) -> str | None:
    """The caption of a message, escaped and bounded, or None when it has none."""
    if not message.caption or not message.caption.strip():
        return None
    return notification_formatter.escape_and_truncate(
        lm, lang, message.caption, MAX_EVIDENCE_CAPTION_LENGTH
    )


def _evidence_details_html(
    lm: LanguageManager, lang: Language, message: Message, attachment: AttachmentInfo
) -> str:
    """Describes an attachment that could not be sent to the owner: type, name, size and
    caption."""
    mention = notification_formatter.user_mention(lm, lang, message.from_user)
    html = [lm.get(lang, "secretary", "notification_evidence_file", mention)]
    file_type = (
        notification_formatter.escape_and_truncate(lm, lang, attachment.mime_type, 128)
        if attachment.mime_type and attachment.mime_type.strip()
        else lm.get(lang, "secretary", attachment.type_key)
    )
    html.append(lm.get(lang, "secretary", "evidence_file_type", file_type))
    if attachment.file_name and attachment.file_name.strip():
        name = notification_formatter.escape_and_truncate(lm, lang, attachment.file_name, 256)
        html.append(lm.get(lang, "secretary", "evidence_file_name", name))
    if attachment.file_size is not None:
        html.append(
            lm.get(lang, "secretary", "evidence_file_size", _human_size(attachment.file_size))
        )
    caption = _evidence_caption(lm, lang, message)
    if caption is not None:
        html.append(lm.get(lang, "secretary", "evidence_file_caption", caption))
    return "".join(html)


def _attachment_info(message: Message) -> AttachmentInfo | None:
    """The details of the file a message carries, or None when it carries none."""
    for media, type_key in (
        (message.document, "evidence_type_document"),
        (message.video, "evidence_type_video"),
        (message.animation, "evidence_type_animation"),
        (message.audio, "evidence_type_audio"),
    ):
        if media is not None:
            return AttachmentInfo(
                media.file_id, type_key, media.file_name, media.mime_type, media.file_size
            )
    if message.voice is not None:
        voice = message.voice
        return AttachmentInfo(
            voice.file_id, "evidence_type_voice", None, voice.mime_type, voice.file_size
        )
    for media, type_key in (
        (message.video_note, "evidence_type_video_note"),
        (message.sticker, "evidence_type_sticker"),
        (message.photo, "evidence_type_photo"),
    ):
        if media is not None:
            return AttachmentInfo(media.file_id, type_key, None, None, media.file_size)
    return None


def _notification_html(
    lm: LanguageManager,
    lang: Language,
    suggestion: SuggestedAction,
    message: Message,
    deleted: bool,
    embed_content: bool,
) -> str:
    """The owner notification about a flagged message."""
    title = "notification_deleted_title" if deleted else "notification_flagged_title"
    mention = notification_formatter.user_mention(lm, lang, message.from_user)
    html = [lm.get(lang, "secretary", title)]
    html.append(lm.get(lang, "secretary", "notification_offender", mention))
    content = message_content.text_or_caption(message) if embed_content else None
    if content is not None and content.strip():
        bounded = notification_formatter.escape_and_truncate(
            lm, lang, content, notification_formatter.MAX_DETAIL_LENGTH
        )
        html.append(lm.get(lang, "secretary", "notification_content", bounded))
    suggested = message_helper.display_name(suggestion)
    html.append(lm.get(lang, "secretary", "notification_suggestion", suggested))
    action = "notification_action_deleted" if deleted else "notification_action_notified"
    html.append(lm.get(lang, "secretary", action))
    return "".join(html)


async def _notify_allowed_owner(
    context: HandlerContext, owner_id: int, message: Message, entity_uuid: str | None
) -> None:
    """Tells the owner a new contact was allowed, with buttons to deny them instead.

    Args:
        entity_uuid: The contact's Federation entity, linked from the notification, or None.
    """
    lm = context.languages
    lang = context.managers.language_preferences.get_user_language(owner_id)
    mention = notification_formatter.user_mention(lm, lang, message.from_user)
    html = (
        lm.get(lang, "secretary", "notification_allowed_title")
        + lm.get(lang, "secretary", "notification_contact", mention)
        + lm.get(lang, "secretary", "notification_allowed_body")
    )
    decision = ContactDecision(
        message.business_connection_id,
        message.from_user.id,
        lang,
        SecretaryContactStatus.ALLOWED,
    )
    await Handler.try_execute(
        "secretary-contact-allowed",
        context.telegram_client.send_message,
        chat_id=owner_id,
        text=html,
        parse_mode=ParseMode.HTML,
        reply_markup=FederationWebLinks.attach(
            _with_report_button(
                context, lang, owner_id, message, contact_decision_markup(lm, decision)
            ),
            context.web_links.button(lm, lang, Record.ENTITY, entity_uuid),
        ),
    )


async def _notify_owner(
    context: HandlerContext,
    owner_id: int,
    message: Message,
    suggestion: SuggestedAction,
    deleted: bool,
    contact_status: SecretaryContactStatus,
    entity_uuid: str | None,
) -> None:
    """Tells the owner about a flagged message, as a reply to a copy of what was flagged.

    Args:
        entity_uuid: The contact's Federation entity, linked from the notification, or None.
    """
    lm = context.languages
    lang = context.managers.language_preferences.get_user_language(owner_id)
    evidence_id = await _echo_flagged_content(context, owner_id, message, lang)
    html = _notification_html(lm, lang, suggestion, message, deleted, evidence_id is None)
    decision = ContactDecision(
        message.business_connection_id, message.from_user.id, lang, contact_status
    )
    await Handler.try_execute(
        "secretary-notification",
        context.telegram_client.send_message,
        chat_id=owner_id,
        text=html,
        parse_mode=ParseMode.HTML,
        reply_markup=FederationWebLinks.attach(
            _with_report_button(
                context, lang, owner_id, message, contact_decision_markup(lm, decision)
            ),
            context.web_links.button(lm, lang, Record.ENTITY, entity_uuid),
        ),
        reply_parameters=(
            ReplyParameters(message_id=evidence_id) if evidence_id is not None else None
        ),
    )


async def _echo_flagged_content(
    context: HandlerContext, owner_id: int, message: Message, lang: Language
) -> int | None:
    """Sends the owner a copy of the flagged content: the file itself, its description when it
    cannot be sent, or the text.

    Returns:
        The id of the copy, or None when there was nothing to copy or it could not be sent.
    """
    lm = context.languages
    client = context.telegram_client
    attachment = _attachment_info(message)
    if attachment is None:
        content = message_content.text_or_caption(message)
        if content is None or not content.strip():
            return None
        mention = notification_formatter.user_mention(lm, lang, message.from_user)
        bounded = notification_formatter.escape_and_truncate(
            lm, lang, content, notification_formatter.MAX_DETAIL_LENGTH
        )
        return await _echo(
            client.send_message,
            chat_id=owner_id,
            text=lm.get(lang, "secretary", "notification_evidence", mention, bounded),
            parse_mode=ParseMode.HTML,
        )

    echoed = await _echo_attachment(context, owner_id, message, attachment, lang)
    if echoed is not None:
        return echoed
    return await _echo(
        client.send_message,
        chat_id=owner_id,
        text=_evidence_details_html(lm, lang, message, attachment),
        parse_mode=ParseMode.HTML,
    )


async def _echo_attachment(
    context: HandlerContext,
    owner_id: int,
    message: Message,
    attachment: AttachmentInfo,
    lang: Language,
) -> int | None:
    """Sends the owner the file a message carries again, by its id, in the kind it was sent as."""
    client = context.telegram_client
    captioned = {
        "chat_id": owner_id,
        "caption": _evidence_caption(context.languages, lang, message) or "",
        "parse_mode": ParseMode.HTML,
    }
    if message.photo is not None:
        return await _echo(client.send_photo, photo=attachment.file_id, **captioned)
    if message.video is not None:
        return await _echo(client.send_video, video=attachment.file_id, **captioned)
    if message.animation is not None:
        return await _echo(client.send_animation, animation=attachment.file_id, **captioned)
    if message.audio is not None:
        return await _echo(client.send_audio, audio=attachment.file_id, **captioned)
    if message.voice is not None:
        return await _echo(client.send_voice, voice=attachment.file_id, **captioned)
    if message.video_note is not None:
        return await _echo(client.send_video_note, chat_id=owner_id, video_note=attachment.file_id)
    if message.sticker is not None:
        return await _echo(client.send_sticker, chat_id=owner_id, sticker=attachment.file_id)
    return await _echo(client.send_document, document=attachment.file_id, **captioned)


async def _echo(method: Callable[..., Awaitable[Message]], **kwargs: Any) -> int | None:
    """Sends a copy of flagged content, returning its id, or None when it could not be sent."""
    try:
        sent = await Handler.execute("secretary-evidence", method, **kwargs)
    except RPCError:
        return None
    return sent.id


def _contact_decision_markup(
    lm: LanguageManager, decision: ContactDecision, callback_prefix: str, settings_button: bool
) -> InlineKeyboardMarkup:
    """The Allow and Deny buttons a contact's status leaves open, and optionally a way to the
    secretary settings."""
    callback = f"{callback_prefix}:{decision.connection_id}:{decision.contact_id}"
    buttons = []
    if decision.contact_status is not SecretaryContactStatus.ALLOWED:
        allow = lm.get(decision.lang, "buttons", "allow_contact")
        buttons.append(InlineKeyboardButton(allow, callback_data=f"{callback}:allow"))
    if decision.contact_status is not SecretaryContactStatus.DENIED:
        deny = lm.get(decision.lang, "buttons", "deny_contact")
        buttons.append(InlineKeyboardButton(deny, callback_data=f"{callback}:deny"))

    rows = [buttons]
    if settings_button:
        settings = lm.get(decision.lang, "buttons", "secretary_settings")
        rows.append(
            [InlineKeyboardButton(settings, callback_data=secretary_settings_handler.OPEN_CALLBACK)]
        )
    return InlineKeyboardMarkup(rows)


def _with_report_button(
    context: HandlerContext,
    lang: Language,
    owner_id: int,
    message: Message,
    markup: InlineKeyboardMarkup,
) -> InlineKeyboardMarkup:
    """Appends a Report button to a notification so the owner can report the message to Federation
    even when it was not flagged, which is how spam that slipped past the scan gets reported.

    The message cannot be fetched again later, so it is captured now and the button carries a token
    for it. Reports are submitted as the bot, which the server refuses without client permissions,
    so no button is offered then.

    Returns:
        The keyboard with a Report row appended, or the markup itself when reporting is
        unavailable.
    """
    if not context.federation.is_authenticated:
        return markup

    target = SecretaryReportTarget(
        owner_id,
        message.from_user.id,
        message.id,
        message_content.text_or_caption(message),
        report_attachments.for_report(context, message),
        flat_metadata.of(message),
    )
    token = context.sessions.secretary_reports.store(target)
    button = InlineKeyboardButton(
        context.languages.get(lang, "buttons", "report_message"),
        callback_data=f"{secretary_settings_handler.REPORT_CALLBACK_PREFIX}:{token}",
    )
    return InlineKeyboardMarkup([*markup.inline_keyboard, [button]])


def keep_report_button(
    replacement: InlineKeyboardMarkup, previous: InlineKeyboardMarkup | None
) -> InlineKeyboardMarkup:
    """Returns a replacement decision keyboard followed by the Report row of the keyboard it
    replaces, so allowing or denying the contact does not take away the option to report.

    Returns:
        The replacement, with the previous Report row appended when there was one.
    """
    if previous is None:
        return replacement

    prefix = f"{secretary_settings_handler.REPORT_CALLBACK_PREFIX}:"
    report_rows = [
        row
        for row in previous.inline_keyboard
        if any(
            isinstance(button.callback_data, str) and button.callback_data.startswith(prefix)
            for button in row
        )
    ]
    return InlineKeyboardMarkup([*replacement.inline_keyboard, *report_rows])


def _register_secretary_contact(context: HandlerContext, message: Message, sender: User) -> None:
    try:
        context.managers.secretary_contacts.register_secretary_contact(
            SecretaryContact(
                message.business_connection_id,
                sender.id,
                SecretaryContactStatus.UNKNOWN,
                int(time.time()),
            )
        )
    except DatabaseException as e:
        log.warning(
            "Failed to register secretary contact %s in connection %s: %s",
            sender.id,
            message.business_connection_id,
            e,
        )


def _decision_command(message: Message) -> str | None:
    """The owner's /allow or /deny command a message carries, lowercased, or None."""
    if not message.text or not message.text.strip():
        return None
    command = message.text.split(maxsplit=1)[0].lower()
    if command in ("/allow", "/deny") or command.startswith(("/allow@", "/deny@")):
        return command
    return None


def _set_contact_status(
    context: HandlerContext, connection_id: str, contact_id: int, status: SecretaryContactStatus
) -> bool:
    """Records the decision about a contact.

    Returns:
        True when it was recorded.
    """
    try:
        context.managers.secretary_contacts.set_secretary_contact_status(
            connection_id, contact_id, status
        )
    except DatabaseException as e:
        log.warning(
            "Failed to set secretary contact %s in connection %s to %s: %s",
            contact_id,
            connection_id,
            status.name,
            e,
        )
        return False
    if status is SecretaryContactStatus.DENIED:
        log.info(
            "Contact %s in connection %s was denied; future messages are blocked",
            contact_id,
            connection_id,
        )
    return True


def _claim_contact(context: HandlerContext, connection_id: str, contact_id: int) -> bool:
    """Claims a contact for analysis, so it is decided exactly once."""
    key = f"{CONTACT_CLAIM_CACHE_PREFIX}{connection_id}:{contact_id}"
    if context.cache.get_if_present(key) is not None:
        return False
    context.cache.put(key, True)
    return True


def _release_contact_claim(context: HandlerContext, connection_id: str, contact_id: int) -> None:
    context.cache.remove(f"{CONTACT_CLAIM_CACHE_PREFIX}{connection_id}:{contact_id}")


def _human_size(size: int) -> str:
    """A file size in B, KB, MB, GB or TB, to one decimal place above bytes."""
    value = float(size)
    units = ("B", "KB", "MB", "GB", "TB")
    unit = 0
    while value >= 1_024 and unit < len(units) - 1:
        value /= 1_024
        unit += 1
    return f"{size} B" if unit == 0 else f"{value:.1f} {units[unit]}"
