"""Resolves the moderation action to take on a user entity and applies it.

The action follows the Federation query result and the chat's configured behavior. Both scanning
and join protection use it, keeping the action-resolution and application logic in one place.
"""

import logging

from pyfederation.enums import SuggestedAction
from pyfederation.records import EntityQueryResult

from spb.enums import JoinProtectionBehavior, ModerationAction, ScanningBehavior
from spb.objects.context.handler_context import HandlerContext
from spb.utilities import moderation_actions

log = logging.getLogger(__name__)


def resolve(
    behavior: ScanningBehavior | JoinProtectionBehavior, query: EntityQueryResult | None
) -> ModerationAction:
    """Resolves the action for the scanning or join-protection pipeline: strict chats ban, moderate
    chats restrict, passive chats do nothing."""
    if query is None or behavior in (ScanningBehavior.PASSIVE, JoinProtectionBehavior.PASSIVE):
        return ModerationAction.NONE

    strict = behavior in (ScanningBehavior.STRICT, JoinProtectionBehavior.STRICT)
    if query.suggested_action is SuggestedAction.PERMANENTLY_BLOCK_ENTITY:
        return ModerationAction.PERMANENT_BAN if strict else ModerationAction.PERMANENT_RESTRICT
    if query.suggested_action is SuggestedAction.TEMPORARILY_BLOCK_ENTITY:
        return ModerationAction.TEMPORARY_BAN if strict else ModerationAction.TEMPORARY_RESTRICT
    return ModerationAction.NONE


async def apply(
    context: HandlerContext,
    chat_id: int,
    user_id: int,
    action: ModerationAction,
    query: EntityQueryResult | None,
) -> bool:
    """Applies the resolved action to a user in a chat.

    Args:
        query: The entity query result, whose suggested lift time bounds a temporary action.

    Returns:
        True if the action was applied.
    """
    if action is ModerationAction.NONE:
        return False

    until = None
    if action in (ModerationAction.TEMPORARY_BAN, ModerationAction.TEMPORARY_RESTRICT):
        until = moderation_actions.temporary_restriction_until(
            query.suggested_lift_timestamp if query is not None else None
        )

    if action in (ModerationAction.PERMANENT_BAN, ModerationAction.TEMPORARY_BAN):
        success = await moderation_actions.ban_user(context, chat_id, user_id, until)
    else:
        success = await moderation_actions.restrict_user(context, chat_id, user_id, until)

    if success:
        log.info("Applied %s to user %s in chat %s", action.name, user_id, chat_id)
    return success
