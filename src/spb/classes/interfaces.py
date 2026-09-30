"""Protocols and callable types shared between the bot's components."""

import sqlite3
from collections.abc import Awaitable, Callable
from typing import Protocol, Self, runtime_checkable

from pyfederation.records import ReportRecord
from pyrogram import Client

from spb.objects.bot_identity import BotIdentity
from spb.objects.database.operator_identity import OperatorIdentity

type RowMapper[T] = Callable[[sqlite3.Row], T]
"""Maps one result row to a value."""

type TransactionWork[T] = Callable[[sqlite3.Connection], T]
"""Runs several statements on one connection inside a transaction."""

type ReportSource = Callable[[OperatorIdentity], Awaitable[list[ReportRecord]]]
"""Reads the open reports assigned to an operator."""


class NotificationSink(Protocol):
    """Delivers notifications to the Telegram users of operators."""

    async def send(self, telegram_user_id: int, report: ReportRecord) -> None:
        """Delivers one report to the Telegram user of one operator."""
        ...

    async def send_credential_revoked(self, telegram_user_id: int) -> None:
        """Tells an operator that their stored credential was rejected by the Federation server
        and has been removed."""
        ...


class Session(Protocol):
    """A short-lived dialog held in memory, identified by an opaque hash."""

    @property
    def hash(self) -> str: ...


@runtime_checkable
class TouchableSession(Session, Protocol):
    """A session whose expiry is refreshed each time it is looked up."""

    def touch(self) -> Self:
        """Returns a copy of the session with its expiry refreshed."""
        ...


class TelegramConnection(Protocol):
    """The bot's connection to Telegram, as the rest of the bot sees it."""

    @property
    def client(self) -> Client:
        """The Telegram client used to answer."""
        ...

    @property
    def identity(self) -> BotIdentity:
        """The bot's own identity as Telegram reported it."""
        ...
