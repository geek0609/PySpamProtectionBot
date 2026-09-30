"""The moderation buttons on report notifications."""

from pyrogram.types import Message

from spb.classes.handler import Handler
from spb.classes.notifications import notification_sender
from spb.classes.notifications.report_action_callback import PREFIX, Action, ReportActionCallback
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.objects.context.handler_context import HandlerContext
from spb.objects.report_action_state import ReportActionState
from spb.utilities import moderation_actions

MUTE_DURATION_SECONDS = 3_600
"""How long the "Delete + Mute 1h" action restricts the author for."""


@update_handler(UpdateType.CALLBACK_QUERY, callback_data=(f"{PREFIX}:",))
class ReportActionHandler(Handler):
    """Acts on a reported message when a moderator presses one of the buttons of its report
    notification.

    The buttons carry everything the action needs, so a press is honoured whoever received the
    notification, possibly hours later and in another chat.
    """

    async def handle(self, context: HandlerContext) -> None:
        callback_query = context.current_update.callback_query
        assert callback_query is not None
        lm = context.languages
        lang = self.resolve_language(context, callback_query)
        notification = callback_query.message
        request = ReportActionCallback.parse(callback_query.data)
        if request is None:
            await self.answer(context, callback_query)
            return

        # Checked live rather than trusted from when the notification was sent, since the buttons
        # do not expire and the moderator may have been demoted since.
        await self.refresh_administrators(context, request.chat_id)
        if not self.has_administrator(
            context, request.chat_id, callback_query.from_user.id, request.action.permits
        ):
            not_permitted = lm.get(lang, "report", "action_not_permitted")
            await self.answer_alert(context, callback_query, not_permitted)
            return

        state = context.sessions.report_actions.claim(request.chat_id, request.message_id)
        if state is None:
            processed = lm.get(lang, "report", "already_processed")
            await self.answer_alert(context, callback_query, processed)
            if notification is not None:
                await self.remove_inline_keyboard(context, notification)
            return

        action_succeeded = await _act(context, request)

        await notification_sender.remove_action_buttons(context, state.targets)
        if notification is not None and not _is_tracked(state, notification):
            # The pressed copy is untracked when the state expired or the bot restarted since.
            await self.remove_inline_keyboard(context, notification)

        if action_succeeded:
            completed = lm.get(lang, "report", "action_completed")
            await self.answer(context, callback_query, completed)
        else:
            failure = lm.get(lang, "report", "action_partial_failure")
            await self.answer_alert(context, callback_query, failure)


async def _act(context: HandlerContext, request: ReportActionCallback) -> bool:
    """Applies the moderation action to the reported message and its author.

    Returns:
        True when every step of the action succeeded.
    """
    deleted = await moderation_actions.delete_message(context, request.chat_id, request.message_id)
    if not deleted or request.action is Action.DELETE:
        return deleted
    if request.action is Action.MUTE:
        until = moderation_actions.until_from_now(MUTE_DURATION_SECONDS)
        return await moderation_actions.restrict_user(
            context, request.chat_id, request.author_id, until
        )
    return await moderation_actions.ban_user(context, request.chat_id, request.author_id, None)


def _is_tracked(state: ReportActionState, notification: Message) -> bool:
    """Whether the pressed notification is one of the tracked copies, whose buttons are removed
    along with the rest."""
    return any(
        target.chat_id == notification.chat.id and target.message_id == notification.id
        for target in state.targets
    )
