"""Everything a handler may need while processing one update."""

import re
from dataclasses import dataclass, replace
from typing import Self

from pyrogram import Client
from pyrogram.types import CallbackQuery, Message, User

from spb.classes.bot_services import BotServices
from spb.classes.cache import Cache
from spb.classes.configuration import Configuration
from spb.classes.database import Database
from spb.classes.federation_service import FederationService
from spb.classes.federation_web_links import FederationWebLinks
from spb.classes.language_manager import LanguageManager
from spb.classes.managers.manager_registry import ManagerRegistry
from spb.objects.admin_info import AdminInfo
from spb.objects.bot_identity import BotIdentity
from spb.objects.chat_info import ChatInfo
from spb.objects.session_registry import SessionRegistry
from spb.objects.update import Update

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class HandlerContext:
    """Everything a handler may need while processing one update.

    Handlers declare no dependencies of their own: the dispatcher derives a context per update from
    a shared template through with_update() and passes it to every handler that runs, which keeps
    handlers free of wiring and makes them trivially testable.

    Attributes:
        services: The bot's long-lived services, reached through the properties below.
        update: The incoming update being processed, or None for the template.
    """

    services: BotServices
    update: Update | None = None

    @property
    def telegram_client(self) -> Client:
        """The Telegram client used to answer."""
        return self.services.telegram_client

    @property
    def database(self) -> Database:
        return self.services.database

    @property
    def managers(self) -> ManagerRegistry:
        return self.services.managers

    @property
    def configuration(self) -> Configuration:
        return self.services.configuration

    @property
    def web_links(self) -> FederationWebLinks:
        """The links into the configured Federation Web Application, disabled when none is
        configured."""
        return self.services.web_links

    @property
    def federation(self) -> FederationService:
        """The Federation server, which reports itself unavailable when unconfigured."""
        return self.services.federation

    @property
    def languages(self) -> LanguageManager:
        return self.services.languages

    @property
    def sessions(self) -> SessionRegistry:
        return self.services.sessions

    @property
    def cache(self) -> Cache[str, object]:
        """The shared cache for transient runtime state."""
        return self.services.cache

    @property
    def chat_admins(self) -> Cache[int, list[AdminInfo]]:
        """The per-chat administrator snapshots maintained by the registration handler."""
        return self.services.chat_admins

    @property
    def chat_info(self) -> Cache[int, ChatInfo]:
        """The per-chat name and type snapshots maintained by the registration handler."""
        return self.services.chat_info

    @property
    def bot(self) -> BotIdentity:
        """The bot's own identity as reported by Telegram."""
        return self.services.identity

    def with_update(self, update: Update) -> Self:
        """Returns a copy of this context bound to the given update, sharing every service."""
        return replace(self, update=update)

    @property
    def current_update(self) -> Update:
        """The update being processed, which every context a handler is given carries.

        Raises:
            RuntimeError: If this is the template, which is bound to no update.
        """
        if self.update is None:
            raise RuntimeError("the template context is bound to no update")
        return self.update

    @property
    def bot_user_id(self) -> int:
        return self.bot.id

    @property
    def bot_username(self) -> str:
        """The bot's username without the leading '@', or an empty string when it has none."""
        return self.bot.username

    @property
    def bot_name(self) -> str:
        return self.bot.name

    @property
    def message(self) -> Message | None:
        """The message this update carries, whichever field it arrived in, or None when it carries
        none."""
        if self.update is None:
            return None
        if self.update.message is not None:
            return self.update.message
        if self.update.channel_post is not None:
            return self.update.channel_post
        if self.update.business_message is not None:
            return self.update.business_message
        if self.update.edited_business_message is not None:
            return self.update.edited_business_message
        if self.update.callback_query is not None:
            return self.update.callback_query.message
        return None

    @property
    def callback_query(self) -> CallbackQuery | None:
        """The inline button press this update carries, or None when it carries none."""
        return self.update.callback_query if self.update is not None else None

    @property
    def callback_data(self) -> str | None:
        """The callback data of the inline button that was pressed, or None when the update is not
        a button press."""
        query = self.callback_query
        return query.data if query is not None else None

    @property
    def chat_id(self) -> int | None:
        """The chat the update belongs to, or None when the update is not bound to a chat."""
        message = self.message
        return message.chat.id if message is not None else None

    @property
    def user(self) -> User | None:
        """The user who caused the update, or None when the update has no author (a channel post,
        for instance)."""
        query = self.callback_query
        if query is not None:
            return query.from_user

        if self.update is not None and self.update.chat_join_request is not None:
            return self.update.chat_join_request.from_user

        message = self.message
        return message.from_user if message is not None else None

    @property
    def user_id(self) -> int | None:
        """The id of the user who caused the update, or None when the update has no author."""
        user = self.user
        return user.id if user is not None else None

    @property
    def command_name(self) -> str | None:
        """The invoked command name, lowercased, without the leading slash and without the @botname
        suffix, or None when the update is not a command message."""
        if self.update is None or self.update.message is None:
            return None

        text = self.update.message.text
        if text is None:
            return None

        first = _WHITESPACE.split(text.strip(), maxsplit=1)[0]
        if len(first) < 2 or first[0] != "/":
            return None

        command = first[1:].partition("@")[0]
        return command.lower() if command else None

    @property
    def command_payload(self) -> str | None:
        """The text following the command token, trimmed, or None when the command carries none."""
        if self.update is None or self.update.message is None:
            return None

        text = self.update.message.text
        if text is None:
            return None

        parts = _WHITESPACE.split(text.strip(), maxsplit=1)
        if len(parts) < 2:
            return None

        payload = parts[1].strip()
        return payload or None
