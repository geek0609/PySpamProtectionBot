"""The managers that own what the bot stores."""

from spb.classes.configuration import Configuration
from spb.classes.database import Database
from spb.classes.language_manager import LanguageManager
from spb.classes.managers.chat_configuration_manager import ChatConfigurationManager
from spb.classes.managers.language_preference_manager import LanguagePreferenceManager
from spb.classes.managers.operator_manager import OperatorManager
from spb.classes.managers.secretary_configuration_manager import SecretaryConfigurationManager
from spb.classes.managers.secretary_contact_manager import SecretaryContactManager
from spb.classes.managers.user_manager import UserManager


class ManagerRegistry:
    """The managers that own what the bot stores, one per table.

    Database decides how SQLite is reached; the managers decide what is kept in it. Keeping the two
    apart means a manager can be reasoned about as plain record-keeping, and the schemas and
    the transaction semantics stay in one place that no manager has to know about. This registry is
    the seam between them: it is built once over an open database and handed to every handler
    through the context.

    Each manager caches its hot lookups in memory, so a chat's configuration is read from SQLite
    once rather than on every message.
    """

    def __init__(
        self, database: Database, configuration: Configuration, languages: LanguageManager
    ) -> None:
        """
        Takes the objects the managers work with rather than the individual settings they need:
        the privacy default new chats start with is read off the configuration here, so adding a
        setting a manager cares about does not widen this signature.

        Args:
            database: The open database.
            configuration: The validated process configuration.
            languages: The translations, used to resolve stored language codes.
        """
        self.users = UserManager(database)
        self.operators = OperatorManager(database)
        self.chat_configurations = ChatConfigurationManager(database, configuration.privacy_mode)
        self.secretary_configurations = SecretaryConfigurationManager(database)
        self.secretary_contacts = SecretaryContactManager(database)
        self.language_preferences = LanguagePreferenceManager(database, languages)
