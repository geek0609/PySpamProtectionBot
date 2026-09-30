"""Everything the bot owns for its whole run."""

from datetime import timedelta

from pyrogram import Client

from spb.classes.cache import Cache
from spb.classes.configuration import Configuration
from spb.classes.database import Database
from spb.classes.federation_service import FederationService
from spb.classes.federation_web_links import FederationWebLinks
from spb.classes.interfaces import TelegramConnection
from spb.classes.language_manager import LanguageManager
from spb.classes.managers.manager_registry import ManagerRegistry
from spb.objects.admin_info import AdminInfo
from spb.objects.chat_info import ChatInfo
from spb.objects.session_registry import SessionRegistry

RUNTIME_CACHE_MAX_SIZE = 100_000
"""Maximum number of entries in the runtime cache shared by every handler."""

RUNTIME_CACHE_EXPIRY = timedelta(minutes=10)
"""How long an entry stays in the runtime cache before it is evicted."""

CHAT_ADMIN_CACHE_MAX_SIZE = 10_000
"""Maximum number of chats whose administrator list is kept in memory."""

CHAT_ADMIN_CACHE_EXPIRY = timedelta(seconds=120)
"""How long a cached administrator list stays valid before it is refetched."""

CHAT_INFO_CACHE_MAX_SIZE = 10_000
"""Maximum number of chats whose name and type are kept in memory."""

CHAT_INFO_CACHE_EXPIRY = timedelta(seconds=300)
"""How long a cached chat snapshot stays valid before it is evicted."""


class BotServices:
    """Everything the bot owns for its whole run, assembled from the four things it is given.

    A handler needs a dozen collaborators, but nobody should have to name a dozen collaborators to
    create one. This takes the configuration, the open database, the connected bot and the
    Federation server, and works out the rest for itself: the translations come from the packaged
    files, the managers from the database and that configuration, the session managers and the
    caches from nothing at all.

    That is the same idea as Configuration, which turns one file path into every setting. Give a
    constructor the real things and let it derive the details, rather than making every caller
    spell them out.

    Everything here is created once and shared by every worker task.
    """

    def __init__(
        self,
        configuration: Configuration,
        database: Database,
        federation: FederationService,
        bot: TelegramConnection,
    ) -> None:
        """
        The bot must already have authenticated, since its identity is read here and every
        handler depends on knowing who it is.

        Args:
            configuration: The validated process configuration.
            database: The open database.
            federation: The Federation server, which may report itself unavailable.
            bot: The authenticated Telegram connection.
        """
        self.configuration = configuration
        self.database = database
        self.federation = federation
        self._bot = bot
        self.identity = bot.identity

        self.web_links = FederationWebLinks.from_configuration(configuration)
        self.languages = LanguageManager(configuration.default_language)
        self.managers = ManagerRegistry(database, configuration, self.languages)
        self.sessions = SessionRegistry()
        self.cache: Cache[str, object] = Cache(RUNTIME_CACHE_MAX_SIZE, RUNTIME_CACHE_EXPIRY)
        self.chat_admins: Cache[int, list[AdminInfo]] = Cache(
            CHAT_ADMIN_CACHE_MAX_SIZE, CHAT_ADMIN_CACHE_EXPIRY
        )
        self.chat_info: Cache[int, ChatInfo] = Cache(
            CHAT_INFO_CACHE_MAX_SIZE, CHAT_INFO_CACHE_EXPIRY
        )

    @property
    def telegram_client(self) -> Client:
        """The Telegram client used to answer."""
        return self._bot.client
