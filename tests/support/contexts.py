"""Builds handler contexts for tests.

A context is assembled the way the bot assembles one, from a configuration, a database, a
Federation service and a Telegram connection, so a test exercises the same wiring production does.
Only the Telegram connection is a stand-in, because opening a real one would mean a network.
"""

from pathlib import Path

from pyrogram import Client

from spb.classes.bot_services import BotServices
from spb.classes.database import Database
from spb.classes.federation_service import FederationService
from spb.objects.bot_identity import BotIdentity
from spb.objects.context.handler_context import HandlerContext
from tests.support import configurations
from tests.support.stub_telegram_connection import StubTelegramConnection

BOT = BotIdentity(1000, "testbot", "Test Bot")
"""The identity tests run as."""


def template(
    directory: Path, federation: FederationService | None = None, client: Client | None = None
) -> HandlerContext:
    """Returns a template context over a database in the given directory, with Federation
    unavailable unless a server is given, and no Telegram client unless a stand-in is given."""
    database = Database(directory / "database.db")
    services = BotServices(
        configurations.minimal(directory),
        database,
        federation if federation is not None else FederationService.unavailable(),
        StubTelegramConnection(BOT, client),
    )
    return HandlerContext(services)
