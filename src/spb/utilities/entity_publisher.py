"""Makes sure a Telegram entity exists on the Federation server before an operation that needs it.

Reports and blacklists are refused for an entity the server has never seen, so whoever is about to
submit one publishes its target first. What is published follows the chat the event happened in:
when that chat has privacy mode on, only the bare identifier is sent; otherwise the entity's
properties go with it. A private chat with the bot never applies privacy mode, because whatever the
user sends there, such as a forward to report, is addressed to the bot on purpose.

Publishing is idempotent, and a bare publish omits metadata entirely rather than sending an empty
set, so it never erases properties an earlier, fuller publish recorded.
"""

from pyrogram.types import Chat, User

from spb.objects.context.handler_context import HandlerContext
from spb.utilities import flat_metadata

TELEGRAM_HOST = "telegram.org"
"""Federation host of every Telegram entity."""


async def publish(
    context: HandlerContext, event_chat_id: int, telegram_id: int, source: User | Chat | None
) -> None:
    """Publishes a Telegram user or chat as <id>@telegram.org.

    Args:
        event_chat_id: The chat the event happened in, whose privacy mode decides what is sent.
        telegram_id: The user or chat id to publish.
        source: The user or chat whose properties are sent, or None to publish only the
            identifier.

    Raises:
        FederationException: If the server is unavailable or rejected the call.
    """
    if not context.federation.is_authenticated:
        # Publishing an entity is refused to the bot unconditionally without client permissions,
        # so there is nothing worth attempting here.
        return

    metadata = (
        flat_metadata.of(source)
        if source is not None and not privacy_applies(context, event_chat_id)
        else None
    )
    await context.federation.publish_entity(TELEGRAM_HOST, str(telegram_id), metadata)


def privacy_applies(context: HandlerContext, event_chat_id: int) -> bool:
    """Returns whether privacy mode restricts what may be published about an event in the chat.

    Telegram gives private chats the positive id of the user on the other side, and groups and
    channels negative ids, so the sign alone tells a private chat apart.
    """
    if event_chat_id > 0:
        return False
    return context.managers.chat_configurations.resolve(event_chat_id).privacy_mode
