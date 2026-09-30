"""Records who and what the bot is seeing, before anything decides what to do about it."""

import logging

from pyrogram.enums import ChatMemberStatus
from pyrogram.types import (
    Chat,
    ChatMember,
    ChatMemberUpdated,
    Message,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginUser,
    User,
)

from spb.classes.federation_service import FederationService
from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import DispatchMode, UpdateType
from spb.exceptions import DatabaseException, FederationException
from spb.objects.chat_info import ChatInfo
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.user_identity import UserIdentity
from spb.utilities import flat_metadata, message_helper

log = logging.getLogger(__name__)


@update_handler(
    UpdateType.MESSAGE,
    UpdateType.MY_CHAT_MEMBER,
    UpdateType.CHAT_MEMBER,
    mode=DispatchMode.OBSERVE,
    priority=1000,
)
class RegistrationHandler(Handler):
    """Records who and what the bot is seeing, before anything decides what to do about it.

    Nearly every other handler depends on facts gathered here and nowhere else: who sent a message,
    what a chat is called, which of its members are administrators, and which messages belong to
    the same album. A permission check reads the cached administrator list; a report names the
    reporter from the stored user; scanning acts on the album as a whole. None of that works if the
    bookkeeping has not run.

    So it runs first, as the highest-priority observer, for every message, including commands,
    which is why /start from a brand-new user already knows who they are. It never consumes an
    update and never answers one; it only writes down what it saw.

    It also keeps that bookkeeping current. A membership update that touches an administrator (the
    bot itself or anyone else) drops the chat's cached administrator list, and a basic group that
    Telegram upgrades to a supergroup has its stored configuration and language moved to the
    supergroup's new chat id. Telegram performs that upgrade on its own, for example when a bot's
    individual administrator rights are edited in a basic group.
    """

    async def handle(self, context: HandlerContext) -> None:
        update = context.current_update
        if update.my_chat_member is not None:
            _forget_administrators_on_change(context, update.my_chat_member)
            return
        if update.chat_member is not None:
            _forget_administrators_on_change(context, update.chat_member)
            return

        message = update.message
        if message is None:
            return

        if message.migrate_to_chat_id is not None:
            _migrate_chat(context, message.chat.id, message.migrate_to_chat_id)
        elif message.migrate_from_chat_id is not None:
            _migrate_chat(context, message.migrate_from_chat_id, message.chat.id)

        author = message.from_user
        if author is None:
            log.debug(
                "Update %s carries a message with no author; nothing to record", update.update_id
            )
            return

        try:
            context.managers.users.save_user(
                UserIdentity(author.id, author.username, author.first_name, author.last_name)
            )
        except DatabaseException as e:
            log.warning(
                "Failed to record user %s from update %s: %s", author.id, update.update_id, e
            )

        _detect_language(context, author)
        await cache_administrators(context, message.chat)
        _cache_chat_info(context, message)
        await _push_message_users(context, message)


async def cache_administrators(context: HandlerContext, chat: Chat) -> None:
    """Ensures the administrator list of a group is cached, refetching it when absent or
    expired."""
    if not message_helper.is_group(chat):
        return

    # A failed fetch is logged by the loader and left uncached, so the next update retries.
    await context.chat_admins.get_async(
        chat.id, lambda chat_id: Handler.load_administrators(context, chat_id), None
    )


async def push_user(federation: FederationService, user: User) -> None:
    """Pushes a Telegram user to the Federation server as <id>@telegram.org, with every property of
    the user as metadata (first_name, is_bot, language_code, ...).

    Properties the update did not carry are left out rather than sent as null, because the server
    merges metadata and a null would erase what an earlier, fuller update recorded.
    """
    if not federation.is_authenticated:
        # Publishing an entity is refused to the bot unconditionally without client permissions,
        # so there is nothing worth attempting or logging here.
        return

    try:
        await federation.publish_entity("telegram.org", str(user.id), flat_metadata.of(user))
    except FederationException as e:
        log.warning("Failed to push Federation entity for user %s: %s", user.id, e)


def should_push_entities(context: HandlerContext, message: Message) -> bool:
    """Decides whether the entities a message reveals may be pushed to the Federation server.

    A private chat with the bot always pushes: whatever the user sends there (a forward to report,
    a /info lookup, an operator command) is addressed to the bot on purpose, and every such feature
    needs the entity to exist on the server first. A group pushes only when the bot is enabled
    there and its privacy mode is off.
    """
    if not context.federation.is_available:
        return False
    if message_helper.is_private(message.chat):
        return True
    if not message_helper.is_group(message.chat):
        return False

    configuration = context.managers.chat_configurations.resolve(message.chat.id)
    return configuration.enabled and not configuration.privacy_mode


def _detect_language(context: HandlerContext, user: User) -> None:
    """Saves the language of the user's Telegram client as their preference, when they have not
    chosen one yet."""
    if not user.language_code or not user.language_code.strip():
        return

    # A client language the bot has no translation for resolves to nothing; there is nothing to
    # detect then, and the user keeps the default.
    detected = context.languages.resolve(user.language_code)
    if detected is None:
        return

    preferences = context.managers.language_preferences
    bot_default = context.languages.default_language
    if preferences.get_user_language(user.id) != bot_default or detected == bot_default:
        return

    try:
        preferences.set_user_language(user.id, detected)
    except DatabaseException as e:
        log.debug(
            "Failed to save detected language '%s' for user %s: %s", detected.code, user.id, e
        )


def _forget_administrators_on_change(context: HandlerContext, change: ChatMemberUpdated) -> None:
    """Drops the cached administrator list of a group when a membership change gains or loses an
    administrator, so the next permission check reads Telegram's current list instead of one that
    is up to two minutes out of date."""
    if not message_helper.is_group(change.chat):
        return

    if _is_administrator(change.old_chat_member) or _is_administrator(change.new_chat_member):
        context.chat_admins.remove(change.chat.id)


def _is_administrator(member: ChatMember | None) -> bool:
    return member is not None and member.status in (
        ChatMemberStatus.OWNER,
        ChatMemberStatus.ADMINISTRATOR,
    )


def _migrate_chat(context: HandlerContext, from_chat_id: int, to_chat_id: int) -> None:
    """Carries a basic group's stored state over to the supergroup Telegram upgraded it to.

    The upgrade gives the chat a new id, and Telegram reports it twice: a message in the old group
    naming the new id, and a message in the new supergroup naming the old one. Either may arrive
    first, so the move is idempotent and never overwrites state the new id already has. Without it
    the upgraded group would look like a chat the bot has never been set up in.
    """
    try:
        context.managers.chat_configurations.migrate_chat(from_chat_id, to_chat_id)
        context.managers.language_preferences.migrate_chat(from_chat_id, to_chat_id)
        log.info(
            "Chat %s was upgraded to supergroup %s; moved its configuration",
            from_chat_id,
            to_chat_id,
        )
    except DatabaseException as e:
        log.warning(
            "Failed to move the configuration of chat %s to supergroup %s: %s",
            from_chat_id,
            to_chat_id,
            e,
        )
    context.chat_admins.remove(from_chat_id)
    context.chat_admins.remove(to_chat_id)
    context.chat_info.remove(from_chat_id)


def _cache_chat_info(context: HandlerContext, message: Message) -> None:
    """Caches the name, id and type of a group for display in configuration menus, which configure
    only groups."""
    chat = message.chat
    if not message_helper.is_group(chat):
        return

    name = chat.title
    if not name or not name.strip():
        name = chat.username
    if not name or not name.strip():
        name = f"Chat {chat.id}"

    context.chat_info.put(chat.id, ChatInfo(chat.id, name, chat.type))


async def _push_message_users(context: HandlerContext, message: Message) -> None:
    """Pushes every user, and every forwarded-from chat, reachable from the message as a Federation
    entity when should_push_entities() allows it.

    The entity address uses the Telegram convention <id>@telegram.org. Pushing is best-effort:
    failures are logged and never prevent the update from being routed.
    """
    if not should_push_entities(context, message):
        return

    for user in _collect_users(message):
        await push_user(context.federation, user)
    for chat in _collect_forwarded_chats(message):
        await _push_chat(context.federation, chat)


async def _push_chat(federation: FederationService, chat: Chat) -> None:
    """Pushes a Telegram chat (a channel or group a message was forwarded from) to the Federation
    server as <chat id>@telegram.org, the same address a report against it uses."""
    if not federation.is_authenticated:
        return

    try:
        await federation.publish_entity("telegram.org", str(chat.id), flat_metadata.of(chat))
    except FederationException as e:
        log.warning("Failed to push Federation entity for chat %s: %s", chat.id, e)


def _collect_users(message: Message) -> list[User]:
    """Collects the distinct users reachable from the message."""
    users: dict[int, User] = {}
    candidates = [
        message.from_user,
        _forward_origin_user(message),
        message.via_bot,
        message.left_chat_member,
        message.sender_business_bot,
        message.guest_bot_caller_user,
        *(message.new_chat_members or ()),
    ]
    reply_to = message.reply_to_message
    if reply_to is not None and not message_helper.is_reply_to_topic_header(message):
        candidates += [reply_to.from_user, _forward_origin_user(reply_to), reply_to.via_bot]

    for user in candidates:
        if user is not None:
            users.setdefault(user.id, user)
    return list(users.values())


def _forward_origin_user(message: Message) -> User | None:
    """Returns the original author of a forwarded message when its origin is a visible user."""
    origin = message.forward_origin
    return origin.sender_user if isinstance(origin, MessageOriginUser) else None


def _collect_forwarded_chats(message: Message) -> list[Chat]:
    """Collects the channels and groups the message, or the message it replies to, was forwarded
    from. Reports against such forwards are addressed to the chat itself."""
    chats: dict[int, Chat] = {}
    sources = [message]
    reply_to = message.reply_to_message
    if reply_to is not None and not message_helper.is_reply_to_topic_header(message):
        sources.append(reply_to)

    for source in sources:
        origin = source.forward_origin
        if isinstance(origin, MessageOriginChannel):
            chats.setdefault(origin.chat.id, origin.chat)
        elif isinstance(origin, MessageOriginChat):
            chats.setdefault(origin.sender_chat.id, origin.sender_chat)
    return list(chats.values())
