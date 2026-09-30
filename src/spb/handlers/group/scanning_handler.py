"""Checks member messages against Federation and acts on what comes back."""

import logging
from dataclasses import dataclass
from typing import cast

from pyfederation.enums import SuggestedAction
from pyfederation.records import EntityQueryResult, ScannedContent
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import RPCError
from pyrogram.types import Message

from spb.classes import update_dispatcher
from spb.classes.federation_web_links import FederationWebLinks
from spb.classes.handler import Handler
from spb.classes.language_manager import LanguageManager
from spb.classes.notifications import notification_formatter, notification_sender
from spb.classes.update_handler import update_handler
from spb.enums import DispatchMode, ModerationAction, ScanningBehavior, UpdateType
from spb.exceptions import FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.chat_configuration import ChatConfiguration
from spb.objects.language import Language
from spb.objects.media_group_state import MediaGroupState
from spb.objects.notification_anchor import NotificationAnchor
from spb.objects.scanning_outcome import ScanningOutcome
from spb.utilities import (
    entity_action_resolver,
    host_extractor,
    message_content,
    message_helper,
    moderation_actions,
)

log = logging.getLogger(__name__)

SCAN_STATE_CACHE_PREFIX = "scan-state:"
TEXT_RESTRICTED_CACHE_PREFIX = "text-restricted:"
PUBLISHED_HOST_CACHE_PREFIX = "published-host:"

type _Suggestions = tuple[SuggestedAction | None, SuggestedAction | None]


@dataclass(slots=True)
class _ScanState:
    """The suggestions of a member's last scanned message.

    Replaced in place rather than stored again, so its cache entry expires counting from the
    member's first scanned message.
    """

    last: _Suggestions | None = None


@update_handler(UpdateType.MESSAGE, mode=DispatchMode.OBSERVE, priority=950)
class ScanningHandler(Handler):
    """Checks member messages against Federation and acts on what comes back.

    Runs as an observer, after RegistrationHandler has recorded who sent the message and refreshed
    the chat's administrator list, both of which the moderation decision depends on. It never
    consumes an update: a /report still reaches its own handler after the message carrying it has
    been scanned.

    What it does with a finding is the chat's scanning behavior: notify only, delete and restrict,
    or delete and ban.

    Alongside the scan, the domain names and IP addresses mentioned in the text or caption are
    published to Federation as host entities, so the database learns about the hosts being linked
    to, not only the users linking them.
    """

    async def handle(self, context: HandlerContext) -> None:
        update = context.current_update
        message = update.message
        if message is None:
            log.debug("Update %s ignored by update handler: no message", update.update_id)
            return

        if message.from_user is None:
            log.debug(
                "Update %s ignored by update handler: message without an author", update.update_id
            )
            return

        await _scan_message_content(context, message)
        await _publish_content_hosts(context, message)

        log.debug("Update %s scanned, author %s", update.update_id, message.from_user.id)


def is_caution_only(
    content_suggestion: SuggestedAction | None, entity_suggestion: SuggestedAction | None
) -> bool:
    """Whether Federation's only concern is a caution, with nothing asking for content or the
    member to be blocked."""
    suggestions = (content_suggestion, entity_suggestion)
    return SuggestedAction.CAUTION in suggestions and all(
        suggestion in (None, SuggestedAction.CAUTION) for suggestion in suggestions
    )


def is_membership_service_message(message: Message) -> bool:
    """Whether the message is a service message announcing members joining or leaving."""
    return bool(message.new_chat_members) or message.left_chat_member is not None


def passive_observation_due(
    context: HandlerContext,
    message: Message,
    content_suggestion: SuggestedAction | None,
    entity_suggestion: SuggestedAction | None,
) -> bool:
    """Decides whether a Passive chat should be notified about a scanned message.

    The member's result is recorded for every message, flagged or not, so a member flagged again
    after a clean message is reported again. Only a flag that differs from the member's previous
    result is worth a notification: a member's first message and a flagged member coming back
    clean also change the result, but carry no flag to report.
    """
    key = f"{SCAN_STATE_CACHE_PREFIX}{message.chat.id}:{message.from_user.id}"
    state = cast(_ScanState, context.cache.get(key, lambda _: _ScanState()))
    previous, state.last = state.last, (content_suggestion, entity_suggestion)
    changed = state.last != previous
    return changed and (content_suggestion is not None or entity_suggestion is not None)


async def _scan_message_content(context: HandlerContext, message: Message) -> None:
    """Scans the message content and queries its author as one moderation operation.

    When scanning is enabled, the bot submits scannable content and, outside Privacy Mode, queries
    the author's Federation entity. The chat's scanning behavior governs automated content
    deletion and entity moderation.
    """
    configuration = context.managers.chat_configurations.resolve(message.chat.id)
    if not _should_moderate(context, message, configuration):
        return

    if _is_known_administrator_or_bot(context, message):
        return

    # A member joining or leaving is posted as a service message with no content. Join protection
    # owns joins; scanning it here would only query the member a second time and, under Passive,
    # post an empty #SCAN_MATCH beside join protection's own notification.
    if is_membership_service_message(message):
        return

    author_id = message.from_user.id
    media_group = update_dispatcher.media_group(context, message)
    if _is_restricted_media_group(media_group, message):
        await moderation_actions.delete_message(context, message.chat.id, message.id)
        return

    scanned_content = None
    if _should_scan(context, message, configuration):
        scanned_content = await _scan(context, message, configuration.privacy_mode)

    entity_query = None
    if not configuration.privacy_mode:
        try:
            entity_query = await context.federation.query_entity(f"{author_id}@telegram.org")
        except FederationException as e:
            log.warning("Failed to query author %s in chat %s: %s", author_id, message.chat.id, e)

    if _is_restricted_media_group(media_group, message):
        await moderation_actions.delete_message(context, message.chat.id, message.id)
        return

    content_suggestion = scanned_content.suggested_action if scanned_content else None
    entity_suggestion = entity_query.suggested_action if entity_query else None

    behavior = configuration.scanning_behavior
    if behavior is not ScanningBehavior.PASSIVE and not await _is_regular_user(context, message):
        log.info(
            "Skipped automated moderation for administrator or bot %s in chat %s",
            author_id,
            message.chat.id,
        )
        return

    entity_action = entity_action_resolver.resolve(behavior, entity_query)
    if not _can_apply_entity_action(entity_action, entity_query):
        entity_action = ModerationAction.NONE

    caution = SuggestedAction.CAUTION in (content_suggestion, entity_suggestion)
    delete_content = _deletes_content(behavior, content_suggestion, caution, message)
    restrict_text = (
        behavior is ScanningBehavior.STRICT
        and entity_suggestion is SuggestedAction.CAUTION
        and not _is_text_restricted(context, message)
    )

    # Passive only ever notifies, and a caution alone is not worth a notification there. The other
    # behaviors notify only about something they act on, which leaves a cautioned plain-text
    # message alone entirely.
    if behavior is ScanningBehavior.PASSIVE:
        # The member's result is recorded before the caution check, so a caution between two
        # identical flags still makes the second one new again.
        if (
            not configuration.scanning_notifications_enabled
            or not passive_observation_due(context, message, content_suggestion, entity_suggestion)
            or is_caution_only(content_suggestion, entity_suggestion)
        ):
            return
    elif not delete_content and entity_action is ModerationAction.NONE and not restrict_text:
        return

    album = (
        media_group
        if delete_content and caution and media_group is not None and media_group.restrict()
        else None
    )
    target_message_ids = sorted(album.message_ids) if album is not None else [message.id]

    # Moderators are shown the message by forwarding it, which Telegram refuses once it is deleted,
    # so it is forwarded to every destination before anything is done to it and the notification
    # replies to that copy afterwards.
    anchors: list[NotificationAnchor] = []
    if configuration.scanning_notifications_enabled:
        anchors = await notification_sender.forward_for_notification(
            context, message.chat.id, target_message_ids
        )

    deleted_message_ids: list[int] = []
    if album is not None:
        for message_id in target_message_ids:
            if await moderation_actions.delete_message(context, message.chat.id, message_id):
                deleted_message_ids.append(message_id)
    elif delete_content and await moderation_actions.delete_message(
        context, message.chat.id, message.id
    ):
        deleted_message_ids = [message.id]
    entity_action_applied = await entity_action_resolver.apply(
        context, message.chat.id, author_id, entity_action, entity_query
    )

    text_restriction_applied = False
    if restrict_text:
        text_restriction_applied = await moderation_actions.restrict_media_only(
            context, message.chat.id, author_id
        )
        if text_restriction_applied:
            context.cache.put(_text_restricted_key(message), True)

    # Sent even when every action failed: the message has already been forwarded, and the
    # notification says what could not be done.
    outcome = ScanningOutcome(
        content_suggestion,
        entity_suggestion,
        tuple(deleted_message_ids),
        delete_content,
        entity_action,
        entity_action_applied,
        text_restriction_applied,
    )
    await _send_scanning_observation(
        context, configuration, message, outcome, anchors, entity_query
    )


async def _scan(
    context: HandlerContext, message: Message, privacy_mode: bool
) -> ScannedContent | None:
    """Submits the message content to Federation's scanner, or returns None when it failed."""
    author_id = message.from_user.id
    try:
        scanned = await context.federation.scan_content(
            message_content.build_scan_input(message, privacy_mode),
            None if privacy_mode else f"{author_id}@telegram.org",
        )
    except FederationException as e:
        log.warning("Failed to scan message %s in chat %s: %s", message.id, message.chat.id, e)
        return None

    log.info(
        "Scanned message %s in chat %s from user %s: risk_score=%s, suggested_action=%s, "
        "classification=%s, resolved_entities=%d, scanner_results=%s",
        message.id,
        message.chat.id,
        author_id,
        scanned.risk_score,
        scanned.suggested_action,
        scanned.classification,
        len(scanned.resolved_entities),
        scanned.scan_results,
    )
    return scanned


async def _publish_content_hosts(context: HandlerContext, message: Message) -> None:
    """Publishes the domain names and IP addresses mentioned in the message as Federation host
    entities.

    Runs after moderation so that publishing never delays a deletion. It follows scanning's
    eligibility, whoever the author is, since recording a host passes no judgement on the person
    who mentioned it. Privacy Mode skips it: there, scanning promises to send nothing but the text
    itself. Hosts published recently are not published again, and failures are logged and skipped:
    one rejected host must not stop the rest.
    """
    configuration = context.managers.chat_configurations.resolve(message.chat.id)
    if (
        configuration.privacy_mode
        or not context.federation.is_authenticated
        or not _should_scan(context, message, configuration)
    ):
        return

    for host in host_extractor.extract(message):
        cache_key = f"{PUBLISHED_HOST_CACHE_PREFIX}{host}"
        if context.cache.get_if_present(cache_key) is not None:
            continue

        try:
            await context.federation.publish_entity(host, None, None)
        except FederationException as e:
            log.warning(
                "Failed to publish host entity %s from message %s in chat %s: %s",
                host,
                message.id,
                message.chat.id,
                e,
            )
            continue
        context.cache.put(cache_key, True)
        log.debug(
            "Published host entity %s from message %s in chat %s", host, message.id, message.chat.id
        )


def _should_scan(
    context: HandlerContext, message: Message, configuration: ChatConfiguration
) -> bool:
    """Whether the message is eligible for Federation scanning: a moderated group with scanning
    enabled, and scannable text or a caption."""
    content = message_content.content_to_scan(message)
    return (
        _should_moderate(context, message, configuration)
        and content is not None
        and bool(content.strip())
    )


def is_linked_channel_post(message: Message) -> bool:
    """Whether the message is a post from the chat's linked channel, which Telegram copies into the
    discussion group as an automatic forward.

    These are the channel's own posts, not a member's message: Telegram attributes them to its
    service account, so without this check they would be scanned and that account queried as if it
    were a member. They are never scanned or moderated.
    """
    return bool(message.automatic_forward)


def _should_moderate(
    context: HandlerContext, message: Message, configuration: ChatConfiguration
) -> bool:
    """Whether scanning moderates the chat: an enabled group with scanning on, Federation
    configured, and the bot one of the chat's moderators. Posts from the linked channel are never
    moderated."""
    if (
        not message_helper.is_group(message.chat)
        or is_linked_channel_post(message)
        or not configuration.enabled
        or not configuration.scanning_enabled
        or not context.federation.is_available
    ):
        return False

    administrators = context.chat_admins.get_if_present(message.chat.id)
    return administrators is not None and any(
        admin.id == context.bot_user_id and admin.is_moderator for admin in administrators
    )


def _deletes_content(
    behavior: ScanningBehavior,
    content_suggestion: SuggestedAction | None,
    caution: bool,
    message: Message,
) -> bool:
    """Decides whether flagged content is deleted under the configured behavior.

    Blocked content is deleted by Moderate and Strict behaviors. Cautioned content is deleted only
    when it is anything other than a plain text message without URLs: photos, videos, stickers,
    other media, links, or messages without text are removed, while basic text messages with no
    URLs are kept. Passive never deletes.
    """
    if behavior is ScanningBehavior.PASSIVE:
        return False
    if content_suggestion is SuggestedAction.BLOCK_CONTENT:
        return True
    return caution and _not_plain_text(message)


def _not_plain_text(message: Message) -> bool:
    """Whether the message carries media or links, or no text at all."""
    text = message_content.content_to_scan(message)
    return (
        message_content.has_media(message)
        or message_content.has_link(message)
        or text is None
        or not text.strip()
    )


def _is_text_restricted(context: HandlerContext, message: Message) -> bool:
    return context.cache.get_if_present(_text_restricted_key(message)) is True


def _text_restricted_key(message: Message) -> str:
    return f"{TEXT_RESTRICTED_CACHE_PREFIX}{message.chat.id}:{message.from_user.id}"


def _can_apply_entity_action(action: ModerationAction, query: EntityQueryResult | None) -> bool:
    """Whether a resolved entity action may be applied: a permanent action always, a temporary one
    only when the Federation server supplies a suggested lift time."""
    if action is ModerationAction.NONE:
        return False
    if action in (ModerationAction.PERMANENT_BAN, ModerationAction.PERMANENT_RESTRICT):
        return True
    return query is not None and query.suggested_lift_timestamp is not None


def _is_known_administrator_or_bot(context: HandlerContext, message: Message) -> bool:
    """Whether the author is a bot or one of the chat's cached moderators."""
    author = message.from_user
    if author.is_bot:
        return True
    administrators = context.chat_admins.get_if_present(message.chat.id)
    return administrators is not None and any(
        admin.id == author.id and admin.is_moderator for admin in administrators
    )


async def _is_regular_user(context: HandlerContext, message: Message) -> bool:
    """Whether the author is a regular member, neither owner, administrator nor bot, as Telegram
    reports now; a failed check counts as not regular, so nobody is moderated on a guess."""
    if _is_known_administrator_or_bot(context, message):
        return False
    try:
        member = await context.telegram_client.get_chat_member(
            message.chat.id, message.from_user.id
        )
    except RPCError as e:
        log.warning(
            "Unable to verify whether user %s is an administrator in chat %s; skipping "
            "moderation: %s",
            message.from_user.id,
            message.chat.id,
            e,
        )
        return False
    return member.status not in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)


def _is_restricted_media_group(media_group: MediaGroupState | None, message: Message) -> bool:
    """Whether the message belongs to an album of its author that was already acted on."""
    return (
        media_group is not None
        and media_group.is_restricted
        and media_group.author_id == message.from_user.id
    )


async def _send_scanning_observation(
    context: HandlerContext,
    configuration: ChatConfiguration,
    message: Message,
    outcome: ScanningOutcome,
    anchors: list[NotificationAnchor],
    entity_query: EntityQueryResult | None,
) -> None:
    """Notifies the chat's moderators of what the scan found and did, as replies to the copies of
    the message forwarded to them, with a button opening the author's entity in the Federation Web
    Application.

    Args:
        anchors: The destinations the message was forwarded to before it was acted on; the
            notification replies to each forwarded copy.
        entity_query: The author's entity query, linked from the notification, or None when the
            author was not queried.
    """
    if not configuration.scanning_notifications_enabled:
        return

    lm = context.languages
    lang = context.managers.language_preferences.get_chat_language(message.chat.id)
    markup = FederationWebLinks.attach(
        None, context.web_links.entity_button(lm, lang, entity_query)
    )

    html = _scanning_notification_html(lm, lang, configuration, message, outcome)
    await notification_sender.notify_anchored(context, anchors, html, markup)


def _scanning_notification_html(
    lm: LanguageManager,
    lang: Language,
    configuration: ChatConfiguration,
    message: Message,
    outcome: ScanningOutcome,
) -> str:
    recommendation = []
    if outcome.content_suggestion is not None:
        recommendation.append(message_helper.display_name(outcome.content_suggestion) + ".")
    if outcome.entity_suggestion is not None:
        member = message_helper.display_name(outcome.entity_suggestion)
        recommendation.append(lm.get(lang, "scanning", "member_prefix", member))

    chat = notification_formatter.chat_reference(lm, lang, message.chat.title, message.chat.id)
    offender = notification_formatter.user_mention(lm, lang, message.from_user)
    taken = _scanning_action_taken(lm, lang, configuration, outcome)
    return (
        _scanning_notification_title(lm, lang, outcome)
        + "\n"
        + lm.get(lang, "scanning", "chat_label", chat)
        + "\n"
        + lm.get(lang, "scanning", "offender_label", offender)
        + "\n"
        + lm.get(lang, "scanning", "action_recommendation", " ".join(recommendation))
        + "\n"
        + lm.get(lang, "scanning", "action_taken", taken)
        + "\n\n"
    )


def _scanning_notification_title(
    lm: LanguageManager, lang: Language, outcome: ScanningOutcome
) -> str:
    if outcome.deleted_message_ids:
        key = "title_content_deleted"
    elif outcome.entity_action_applied and outcome.entity_action in (
        ModerationAction.PERMANENT_BAN,
        ModerationAction.TEMPORARY_BAN,
    ):
        key = "title_member_banned"
    elif outcome.entity_action_applied or outcome.text_restriction_applied:
        key = "title_member_restricted"
    else:
        key = "title_scan_match"
    return lm.get(lang, "scanning", key)


def _scanning_action_taken(
    lm: LanguageManager,
    lang: Language,
    configuration: ChatConfiguration,
    outcome: ScanningOutcome,
) -> str:
    """Summarises what was done about the message and its author."""
    actions = []
    if outcome.deleted_message_ids:
        ids = ", ".join(f"<code>{message_id}</code>" for message_id in outcome.deleted_message_ids)
        single = len(outcome.deleted_message_ids) == 1
        key = "action_deleted_single" if single else "action_deleted_multiple"
        actions.append(lm.get(lang, "scanning", key, ids))
    elif outcome.content_removal_requested:
        actions.append(lm.get(lang, "scanning", "action_delete_failed"))
    if outcome.text_restriction_applied:
        actions.append(lm.get(lang, "scanning", "action_restricted_text"))
    if outcome.entity_action is not ModerationAction.NONE:
        if outcome.entity_action_applied:
            actions.append(lm.get(lang, "scanning", _ENTITY_ACTION_TAKEN[outcome.entity_action]))
        else:
            description = lm.get(
                lang, "scanning", _ENTITY_ACTION_DESCRIPTION[outcome.entity_action]
            )
            actions.append(lm.get(lang, "scanning", "action_failed_prefix", description))
    if not actions:
        passive = configuration.scanning_behavior is ScanningBehavior.PASSIVE
        key = "action_notification_only" if passive else "action_none_required"
        return lm.get(lang, "scanning", key)
    return "; ".join(actions) + "."


_ENTITY_ACTION_TAKEN = {
    ModerationAction.TEMPORARY_BAN: "entity_action_temp_banned",
    ModerationAction.PERMANENT_BAN: "entity_action_banned",
    ModerationAction.TEMPORARY_RESTRICT: "entity_action_temp_restricted",
    ModerationAction.PERMANENT_RESTRICT: "entity_action_restricted",
}

_ENTITY_ACTION_DESCRIPTION = {
    ModerationAction.TEMPORARY_BAN: "entity_desc_temp_ban",
    ModerationAction.PERMANENT_BAN: "entity_desc_ban",
    ModerationAction.TEMPORARY_RESTRICT: "entity_desc_temp_restrict",
    ModerationAction.PERMANENT_RESTRICT: "entity_desc_restrict",
}
