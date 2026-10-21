"""The process entry point."""

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from spb.classes.bot_services import BotServices
from spb.classes.command_line_options import CommandLineOptions, usage
from spb.classes.configuration import Configuration
from spb.classes.database import Database
from spb.classes.federation_service import FederationService
from spb.classes.handler_registry import HandlerRegistry
from spb.classes.notifications.notification_service import NotificationService
from spb.classes.telegram_bot import TelegramBot
from spb.classes.update_dispatcher import UpdateDispatcher
from spb.exceptions import CommandLineException, ConfigurationException, FederationException
from spb.objects.context.handler_context import HandlerContext
from spb.utilities import host_canonicalizer

log = logging.getLogger(__name__)

FEDERATION_AUTHENTICATION_RETRY = 30
"""Seconds between attempts to authenticate with a Federation server that has not answered yet."""

SUPPORTED_FEDERATION_API_MAJOR_VERSION = 2
"""The major version of OFD the bot implements."""


class Main:
    """The running bot: the database, the Federation and Telegram connections, the dispatcher and
    the notification poller, started in dependency order and stopped in reverse."""

    def __init__(self, configuration: Configuration, database_path: Path) -> None:
        """
        Args:
            configuration: The validated configuration.
            database_path: The location of the SQLite database file. The MTProto session is kept
                next to it, under the same name.
        """
        self._configuration = configuration
        self._database_path = database_path
        self._tld_refresh: asyncio.Task[bool] | None = None
        self._database: Database | None = None
        self._federation: FederationService | None = None
        self._bot: TelegramBot | None = None
        self._dispatcher: UpdateDispatcher | None = None
        self._notifications: NotificationService | None = None
        self._federation_authentication: asyncio.Task[None] | None = None
        self._started = False

    async def start(self) -> None:
        """Opens the database, connects to Telegram, registers the handlers, and starts processing
        updates.

        Raises:
            DatabaseException: If the database cannot be created, opened, or initialized.
            pyrogram.errors.RPCError: If the credentials are rejected.
        """
        # Replace the bundled TLD snapshot with IANA's current list. It runs in the background: the
        # bundled list serves until then, and stays in use if the fetch fails.
        self._tld_refresh = asyncio.create_task(
            host_canonicalizer.refresh_tlds(), name="spb-tld-refresh"
        )

        self._database = Database(self._database_path)
        tables = self._database.tables()
        log.info(
            "Database ready at %s with %d table(s): %s",
            self._database.path,
            len(tables),
            ", ".join(tables),
        )

        registry = HandlerRegistry.discover()
        self._federation = await self._connect_federation()
        if self._federation.is_authentication_pending:
            self._schedule_federation_authentication(self._federation)

        self._bot = TelegramBot(self._configuration, self._database_path.with_suffix(".session"))
        await self._bot.authenticate()

        services = BotServices(self._configuration, self._database, self._federation, self._bot)
        self._dispatcher = UpdateDispatcher(
            registry,
            HandlerContext(services),
            self._configuration.worker_threads,
            self._configuration.queue_capacity,
        )
        self._bot.start_receiving(self._dispatcher.submit)

        # Operators are told about the reports assigned to them by polling Federation, which only
        # makes sense once there is a Federation to poll.
        if self._federation.is_available:
            self._notifications = NotificationService.create(
                services.managers,
                self._bot.client,
                self._federation,
                services.languages,
                services.web_links,
                self._configuration.notification_interval_seconds,
            )
            self._notifications.start()

        self._started = True
        log.info(
            "%s started with %d worker task(s) and a queue of %d update(s)",
            self._configuration.name,
            self._configuration.worker_threads,
            self._configuration.queue_capacity,
        )

    async def close(self) -> None:
        """Stops the bot: no further updates are fetched, everything already accepted is processed
        to completion, and the database is closed last."""
        if self._started:
            log.info("Shutting down %s", self._configuration.name)

        # Stop fetching, both the update stream and the report notification poll.
        # Updates already handed to the dispatcher are untouched.
        if self._bot is not None:
            self._bot.stop_receiving()

        if self._federation_authentication is not None:
            self._federation_authentication.cancel()

        if self._notifications is not None:
            await self._notifications.close()

        # Let the queue run out, so every accepted update is finished rather than abandoned.
        if self._dispatcher is not None and await self._dispatcher.drain(
            self._configuration.shutdown_timeout_seconds
        ):
            log.info(
                "Processed %d update(s), %d dropped",
                self._dispatcher.processed_updates,
                self._dispatcher.dropped_updates,
            )

        # Release the transport and the Federation client; nothing needs them any more.
        if self._bot is not None:
            await self._bot.close()

        if self._federation is not None:
            await self._federation.close()

        # Close the database last: until the queue ran out, handlers could still be writing.
        if self._database is not None:
            self._database.close()

        self._started = False
        log.info("Shutdown complete")

    async def _connect_federation(self) -> FederationService:
        """Connects to the Federation server if one is configured.

        An unreachable server is a warning, not a failure: the bot moderates, configures itself,
        and answers commands without Federation, and the features that need it report themselves
        unavailable until it comes back. Authentication is then left pending for
        _schedule_federation_authentication() to retry.
        """
        configuration = self._configuration
        if not configuration.has_federation:
            log.warning(
                "No 'federation' section in the configuration; running without Federation support"
            )
            return FederationService.unavailable()

        service = FederationService(
            configuration.federation_endpoint, configuration.federation_access_token
        )
        try:
            information = await service.server_information()
        except FederationException as e:
            log.warning(
                "Federation server %s is unreachable or returned an error: %s",
                configuration.federation_endpoint,
                e,
            )
            return service

        log.info(
            "Connected to Federation server '%s' (API %s) with %s known entities and %s blacklist "
            "records",
            information.name,
            information.api_version,
            information.known_entities,
            information.blacklist_records,
        )
        if information.api_major_version != SUPPORTED_FEDERATION_API_MAJOR_VERSION:
            log.warning(
                "Federation server reports API version '%s', but this bot implements OFD %d.x; "
                "requests may fail or be misread",
                information.api_version,
                SUPPORTED_FEDERATION_API_MAJOR_VERSION,
            )

        access_token = configuration.federation_access_token
        if access_token is None or not access_token.strip():
            log.info("No Federation access token configured; running as an anonymous client")
            return service

        await self._authenticate_federation(service, retrying=False)
        return service

    def _schedule_federation_authentication(self, service: FederationService) -> None:
        """Retries authentication in the background until the server settles the bot's own access
        token, so a Federation server that was down at startup, or went down mid-authentication, is
        authenticated against as soon as it is reachable again rather than never."""
        log.info(
            "Federation authentication is pending; retrying every %d second(s) until the server "
            "answers",
            FEDERATION_AUTHENTICATION_RETRY,
        )

        async def retry() -> None:
            while True:
                await asyncio.sleep(FEDERATION_AUTHENTICATION_RETRY)
                if not service.is_authentication_pending:
                    return
                await self._authenticate_federation(service, retrying=True)

        self._federation_authentication = asyncio.create_task(
            retry(), name="spb-federation-authentication"
        )

    async def _authenticate_federation(self, service: FederationService, retrying: bool) -> None:
        """Confirms the bot's own access token carries client permissions, logging the outcome.

        Every write the bot makes as itself requires client permissions, so a token without them,
        or one the server rejects, is of no use to the bot. FederationService.authenticate()
        therefore discards it, and the bot is treated exactly like a host that never configured
        one. A failure that says nothing about the token leaves authentication pending.

        Args:
            service: The service to authenticate.
            retrying: Whether this is a background retry, whose repeated connection failures are
                logged quietly so an outage does not flood the log.
        """
        try:
            operator = await service.authenticate()
        except FederationException as e:
            if FederationService.is_credential_rejected(e):
                log.warning(
                    "Federation access token was rejected (%s); continuing as an anonymous client",
                    e,
                )
            elif retrying:
                log.debug("Federation authentication is still pending: %s", e)
            else:
                log.warning("Could not authenticate with Federation (%s); will retry", e)
            return

        if operator is None:
            return
        if service.is_authenticated:
            log.info(
                "Authenticated with Federation as operator '%s' with client permissions",
                operator.name,
            )
            return
        log.warning(
            "Federation access token belongs to operator '%s' but lacks client permissions; "
            "continuing as an anonymous client so publicly available features keep working",
            operator.name,
        )


def main(args: Sequence[str] | None = None) -> int:
    """Runs the bot until it is interrupted.

    Returns:
        The exit status: 0 after a clean shutdown, 1 when the bot failed to start, and 2 for invalid
        options or configuration.
    """
    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d [%(taskName)s] %(levelname)-5s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )
    # httpx logs every request at INFO, which says nothing useful about the bot.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        options = CommandLineOptions.parse(sys.argv[1:] if args is None else args)
    except CommandLineException as e:
        print(e, file=sys.stderr)
        return 2

    if options.help:
        print(usage())
        return 0

    try:
        configuration = Configuration(options.configuration)
    except ConfigurationException as e:
        print(e, file=sys.stderr)
        return 2

    log.info("Loaded configuration from %s", options.configuration.resolve())
    return asyncio.run(_run(Main(configuration, options.database)))


async def _run(bot: Main) -> int:
    """Starts the bot and keeps it running until SIGINT or SIGTERM, then shuts it down."""
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        # Windows has no loop signal handlers; there Ctrl+C cancels this task instead.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, shutdown.set)

    try:
        await bot.start()
    except Exception as e:
        log.exception("Failed to start: %s", e)
        await bot.close()
        return 1

    try:
        await shutdown.wait()
    finally:
        await bot.close()
    return 0
