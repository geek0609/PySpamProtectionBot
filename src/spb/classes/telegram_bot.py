"""The bot's connection to Telegram."""

import itertools
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.handlers import (
    BusinessConnectionHandler,
    BusinessMessageHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberUpdatedHandler,
    DeletedBusinessMessagesHandler,
    EditedBusinessMessageHandler,
    MessageHandler,
)
from pyrogram.types import (
    BusinessConnection,
    CallbackQuery,
    ChatJoinRequest,
    ChatMemberUpdated,
    Message,
)

from spb.classes.configuration import Configuration
from spb.objects.bot_identity import BotIdentity
from spb.objects.update import Update

log = logging.getLogger(__name__)

type UpdateConsumer = Callable[[Update], object]
"""Takes each update the bot receives, such as the dispatcher's submit."""


class TelegramBot:
    """The bot's connection to Telegram: the MTProto client and what it receives.

    This class owns the transport and nothing else. It knows how to reach Telegram, who the bot is,
    and how to hand received updates on; what to do with an update is the dispatcher's business.

    Telegram delivers updates over MTProto as separate kinds of event, so the connection registers
    one kurigram handler per kind the bot's handlers serve and wraps each event in an Update. The
    MTProto session, and with it the point up to which updates were processed, is kept in a file,
    so updates sent while the bot was offline are fetched when it starts again.

    Lifecycle: authenticate() connects and resolves the bot's identity, start_receiving() begins
    handing updates to the consumer, including any that arrived in between, stop_receiving() stops
    handing them on, and close() disconnects. Shutdown goes through stop_receiving() first, so the
    dispatcher can finish its queue knowing nothing new will arrive.
    """

    def __init__(self, configuration: Configuration, session: Path) -> None:
        """
        Args:
            configuration: The validated configuration.
            session: The file the MTProto session is kept in.
        """
        self._client = Client(
            session.stem,
            api_id=configuration.api_id,
            api_hash=configuration.api_hash,
            bot_token=configuration.api_key,
            test_mode=configuration.test_server,
            workdir=str(session.parent),
            # One worker only wraps and hands on updates, which keeps them in arrival order.
            workers=1,
            skip_updates=False,
        )
        self._test_server = configuration.test_server
        self._identity: BotIdentity | None = None
        self._consumer: UpdateConsumer | None = None
        self._held: list[Update] | None = []
        self._update_ids = itertools.count(1)

    @property
    def client(self) -> Client:
        """The Telegram client used to answer."""
        return self._client

    @property
    def identity(self) -> BotIdentity:
        """The bot's own identity, resolved by authenticate()."""
        if self._identity is None:
            raise RuntimeError("authenticate() must succeed before the bot's identity is known")
        return self._identity

    async def authenticate(self) -> BotIdentity:
        """Connects to Telegram with the configured credentials and resolves the bot's identity.

        Updates received from here on are held until start_receiving().

        Raises:
            pyrogram.errors.RPCError: If Telegram rejects the credentials.
        """
        # Registered before connecting: starting fetches the updates missed while offline.
        for handler in (
            MessageHandler(self._on_message),
            CallbackQueryHandler(self._on_callback_query),
            ChatMemberUpdatedHandler(self._on_chat_member_updated),
            ChatJoinRequestHandler(self._on_chat_join_request),
            BusinessConnectionHandler(self._on_business_connection),
            BusinessMessageHandler(self._on_business_message),
            EditedBusinessMessageHandler(self._on_edited_business_message),
            DeletedBusinessMessagesHandler(self._on_deleted_business_messages),
        ):
            self._client.add_handler(handler)

        await self._client.start()
        me = self._client.me
        self._identity = BotIdentity(me.id, me.username or "", me.first_name)

        log.info(
            "Authenticated as %s (%s)",
            f"@{self._identity.username}" if self._identity.has_username else self._identity.id,
            self._identity.name,
        )
        return self._identity

    def start_receiving(self, consumer: UpdateConsumer) -> None:
        """Hands every update received so far, and every update from now on, to the consumer."""
        held = self._held or []
        self._held = None
        self._consumer = consumer
        for update in held:
            consumer(update)

        log.info("Receiving updates from Telegram%s", " (test server)" if self._test_server else "")

    def stop_receiving(self) -> None:
        """Stops handing updates on; whatever was already handed to the consumer is left alone."""
        if self._consumer is None:
            return

        self._consumer = None
        log.info("Stopped receiving updates")

    async def close(self) -> None:
        """Stops receiving updates and disconnects from Telegram."""
        self.stop_receiving()
        self._held = None
        if self._client.is_connected:
            await self._client.stop()

    def _deliver(self, **fields: Any) -> None:
        """Wraps one received event in an Update and hands it on, or holds it until a consumer is
        attached."""
        update = Update(next(self._update_ids), **fields)
        if self._consumer is not None:
            self._consumer(update)
        elif self._held is not None:
            self._held.append(update)

    async def _on_message(self, client: Client, message: Message) -> None:
        if message.chat.type is ChatType.CHANNEL:
            self._deliver(channel_post=message)
        else:
            self._deliver(message=message)

    async def _on_callback_query(self, client: Client, query: CallbackQuery) -> None:
        self._deliver(callback_query=query)

    async def _on_chat_member_updated(self, client: Client, change: ChatMemberUpdated) -> None:
        member = change.new_chat_member or change.old_chat_member
        if member.user.id == self.identity.id:
            self._deliver(my_chat_member=change)
        else:
            self._deliver(chat_member=change)

    async def _on_chat_join_request(self, client: Client, request: ChatJoinRequest) -> None:
        self._deliver(chat_join_request=request)

    async def _on_business_connection(self, client: Client, connection: BusinessConnection) -> None:
        self._deliver(business_connection=connection)

    async def _on_business_message(self, client: Client, message: Message) -> None:
        self._deliver(business_message=message)

    async def _on_edited_business_message(self, client: Client, message: Message) -> None:
        self._deliver(edited_business_message=message)

    async def _on_deleted_business_messages(
        self, client: Client, messages: Sequence[Message]
    ) -> None:
        self._deliver(deleted_business_messages=messages)
