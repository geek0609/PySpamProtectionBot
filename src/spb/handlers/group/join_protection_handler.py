"""Checks members against Federation as they join, and acts on what it finds."""

import logging
from typing import TypeGuard

from pyfederation.enums import SuggestedAction
from pyfederation.records import EntityQueryResult
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import Chat, ChatJoinRequest, ChatMember, ChatMemberUpdated, User

from spb.classes.federation_web_links import FederationWebLinks
from spb.classes.handler import Handler
from spb.classes.language_manager import LanguageManager
from spb.classes.notifications import notification_formatter, notification_sender
from spb.classes.update_handler import update_handler
from spb.enums import DispatchMode, ModerationAction, UpdateType
from spb.exceptions import FederationException
from spb.handlers.group import registration_handler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.language import Language
from spb.utilities import entity_action_resolver, message_helper, moderation_actions

log = logging.getLogger(__name__)

JOIN_CLAIM_CACHE_PREFIX = "join-protection:"


@update_handler(
    UpdateType.MESSAGE,
    UpdateType.CHAT_MEMBER,
    UpdateType.CHAT_JOIN_REQUEST,
    mode=DispatchMode.OBSERVE,
    priority=900,
)
class JoinProtectionHandler(Handler):
    """Checks members against Federation as they join, and acts on what it finds.

    Runs as an observer after ScanningHandler, so the member it is deciding about has already been
    registered and the chat's administrator list is current. Joins arrive in three shapes, a
    service message, a membership update, or a join request, and all three are declared here so
    the chat is protected however Telegram reports the event.
    """

    async def handle(self, context: HandlerContext) -> None:
        update = context.current_update
        if update.chat_member is not None:
            membership = update.chat_member
            if _is_joining_member(membership):
                await _protect_joining_member(
                    context, membership.chat, membership.new_chat_member.user, True
                )
            return

        if update.chat_join_request is not None:
            await _protect_join_request(context, update.chat_join_request)
            return

        message = update.message
        if message is None or not message.new_chat_members:
            return
        for member in message.new_chat_members:
            await _protect_joining_member(context, message.chat, member, False)


async def _protect_joining_member(
    context: HandlerContext, chat: Chat, member: User, membership_update: bool
) -> None:
    if member.is_bot or not message_helper.is_group(chat):
        return

    configuration = context.managers.chat_configurations.resolve(chat.id)
    federation = context.federation
    if (
        not configuration.enabled
        or not configuration.join_protection_enabled
        or not federation.is_available
        or not _claim_join(context, chat.id, member.id)
    ):
        return

    if membership_update and not configuration.privacy_mode:
        await registration_handler.push_user(federation, member)

    try:
        query = await federation.query_entity(f"{member.id}@telegram.org")
    except FederationException as e:
        context.cache.remove(_join_cache_key(chat.id, member.id))
        log.warning("Failed to query newly joined member %s in chat %s: %s", member.id, chat.id, e)
        return

    action = entity_action_resolver.resolve(configuration.join_protection_behavior, query)
    action_applied = await entity_action_resolver.apply(context, chat.id, member.id, action, query)
    if _is_block_recommendation(query) and configuration.join_protection_notifications_enabled:
        if configuration.moderator_notifications_enabled:
            await registration_handler.cache_administrators(context, chat)
        lang = context.managers.language_preferences.get_chat_language(chat.id)
        await notification_sender.notify(
            context,
            chat.id,
            _notification_html(
                context.languages, lang, chat, member, query, action, action_applied
            ),
            FederationWebLinks.attach(
                None, context.web_links.entity_button(context.languages, lang, query)
            ),
        )


async def _protect_join_request(context: HandlerContext, join_request: ChatJoinRequest) -> None:
    """Decides a join request against Federation, declining it when the chat's behaviour acts on
    the user and approving it otherwise, and tells the moderators what was decided."""
    chat = join_request.chat
    user = join_request.from_user
    if user.is_bot or not message_helper.is_group(chat):
        return

    configuration = context.managers.chat_configurations.resolve(chat.id)
    federation = context.federation
    if (
        not configuration.enabled
        or not configuration.join_protection_enabled
        or not federation.is_available
        or not _claim_join(context, chat.id, user.id)
    ):
        return

    if not configuration.privacy_mode:
        await registration_handler.push_user(federation, user)

    try:
        query = await federation.query_entity(f"{user.id}@telegram.org")
    except FederationException as e:
        context.cache.remove(_join_cache_key(chat.id, user.id))
        log.warning("Failed to query join request user %s in chat %s: %s", user.id, chat.id, e)
        return

    action = entity_action_resolver.resolve(configuration.join_protection_behavior, query)

    # Moderate may decline join requests by blocking a flagged member, and Strict bans them.
    # Passive takes no automated action, so the request is approved for the moderator to review.
    declined = action is not ModerationAction.NONE
    if declined:
        request_handled = await moderation_actions.decline_join_request(context, chat.id, user.id)
    else:
        request_handled = await moderation_actions.approve_join_request(context, chat.id, user.id)

    notify_action = (request_handled and declined) or _is_block_recommendation(query)
    if notify_action and configuration.join_protection_notifications_enabled:
        if configuration.moderator_notifications_enabled:
            await registration_handler.cache_administrators(context, chat)
        lang = context.managers.language_preferences.get_chat_language(chat.id)
        await notification_sender.notify(
            context,
            chat.id,
            _join_request_notification_html(
                context.languages, lang, chat, user, query, declined, request_handled
            ),
            FederationWebLinks.attach(
                None, context.web_links.entity_button(context.languages, lang, query)
            ),
        )


def _notification_html(
    lm: LanguageManager,
    lang: Language,
    chat: Chat,
    member: User,
    query: EntityQueryResult,
    action: ModerationAction,
    action_applied: bool,
) -> str:
    """Builds the notification about a flagged member who joined."""
    chat_reference = notification_formatter.chat_reference(lm, lang, chat.title, chat.id)
    offender = notification_formatter.user_mention(lm, lang, member)
    recommendation = message_helper.display_name(query.suggested_action) + "."
    taken = _notification_action(lm, lang, action, action_applied)
    return (
        _join_notification_title(lm, lang, action, action_applied)
        + "\n"
        + lm.get(lang, "join_protection", "chat_label", chat_reference)
        + "\n"
        + lm.get(lang, "join_protection", "offender_label", offender)
        + "\n"
        + lm.get(lang, "join_protection", "action_recommendation", recommendation)
        + "\n"
        + lm.get(lang, "join_protection", "action_taken", taken)
        + "\n\n"
        + lm.get(lang, "join_protection", "no_content")
    )


def _join_request_notification_html(
    lm: LanguageManager,
    lang: Language,
    chat: Chat,
    user: User,
    query: EntityQueryResult | None,
    declined: bool,
    request_handled: bool,
) -> str:
    """Builds the notification about a join request that was declined or approved."""
    title = lm.get(lang, "join_protection", "request_declined" if declined else "request_approved")
    chat_reference = notification_formatter.chat_reference(lm, lang, chat.title, chat.id)
    html = [title + "\n"]
    html.append(lm.get(lang, "join_protection", "chat_label", chat_reference) + "\n")
    user_mention = notification_formatter.user_mention(lm, lang, user)
    html.append(lm.get(lang, "join_protection", "user_label", user_mention) + "\n")
    if _is_block_recommendation(query):
        recommendation = message_helper.display_name(query.suggested_action) + "."
        html.append(lm.get(lang, "join_protection", "action_recommendation", recommendation) + "\n")

    if not request_handled:
        taken = "request_action_failed"
    elif declined:
        taken = "request_action_declined"
    else:
        taken = "request_action_approved"
    html.append(
        lm.get(lang, "join_protection", "action_taken", lm.get(lang, "join_protection", taken))
        + "\n"
    )
    return "".join(html)


def _is_block_recommendation(query: EntityQueryResult | None) -> TypeGuard[EntityQueryResult]:
    """Whether the query recommends blocking the entity, temporarily or permanently."""
    return query is not None and query.suggested_action in (
        SuggestedAction.TEMPORARILY_BLOCK_ENTITY,
        SuggestedAction.PERMANENTLY_BLOCK_ENTITY,
    )


def _is_joining_member(membership: ChatMemberUpdated) -> bool:
    """Whether a membership change takes someone from outside the chat into it."""
    return _is_not_member(membership.old_chat_member) and _is_member(membership.new_chat_member)


def _is_member(member: ChatMember | None) -> bool:
    if member is None:
        return False
    if member.status is ChatMemberStatus.RESTRICTED:
        return member.is_member is True
    return member.status is ChatMemberStatus.MEMBER


def _is_not_member(member: ChatMember | None) -> bool:
    if member is None:
        return False
    if member.status is ChatMemberStatus.RESTRICTED:
        return member.is_member is not True
    return member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED)


def _claim_join(context: HandlerContext, chat_id: int, member_id: int) -> bool:
    """Claims a join for the caller, so the same join reported as a service message and as a
    membership update is acted on once."""
    key = _join_cache_key(chat_id, member_id)
    if context.cache.get_if_present(key) is not None:
        return False
    context.cache.put(key, True)
    return True


def _join_cache_key(chat_id: int, member_id: int) -> str:
    return f"{JOIN_CLAIM_CACHE_PREFIX}{chat_id}:{member_id}"


def _join_notification_title(
    lm: LanguageManager, lang: Language, action: ModerationAction, action_applied: bool
) -> str:
    key = "title_blocked"
    if action_applied:
        if action in (ModerationAction.TEMPORARY_BAN, ModerationAction.PERMANENT_BAN):
            key = "title_banned"
        elif action in (ModerationAction.TEMPORARY_RESTRICT, ModerationAction.PERMANENT_RESTRICT):
            key = "title_restricted"
    return lm.get(lang, "join_protection", key)


def _notification_action(
    lm: LanguageManager, lang: Language, action: ModerationAction, action_applied: bool
) -> str:
    if action is ModerationAction.NONE:
        key = "action_none_passive"
    elif not action_applied:
        key = "action_failed"
    else:
        key = {
            ModerationAction.TEMPORARY_RESTRICT: "action_temp_restricted",
            ModerationAction.PERMANENT_RESTRICT: "action_restricted",
            ModerationAction.TEMPORARY_BAN: "action_temp_banned",
            ModerationAction.PERMANENT_BAN: "action_banned",
        }[action]
    return lm.get(lang, "join_protection", key)
