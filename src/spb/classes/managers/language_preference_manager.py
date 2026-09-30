"""Manager for the language_preferences table."""

import logging
from datetime import timedelta

from spb.classes.cache import Cache, LoadFailedException
from spb.classes.database import Database
from spb.classes.language_manager import LanguageManager
from spb.exceptions import DatabaseException
from spb.objects.language import Language

log = logging.getLogger(__name__)

CACHE_MAX_SIZE = 100_000
CACHE_EXPIRY = timedelta(minutes=10)

TYPE_CHAT = "CHAT"
TYPE_USER = "USER"


class LanguagePreferenceManager:
    """Manager for the language_preferences table, which holds the language each chat and user
    chose.

    A preference that is not set, or names a language that no longer has a translation file,
    resolves to the bot's default language.
    """

    def __init__(self, database: Database, languages: LanguageManager) -> None:
        self._database = database
        self._languages = languages
        self._chat_cache: Cache[int, Language] = Cache(CACHE_MAX_SIZE, CACHE_EXPIRY)
        self._user_cache: Cache[int, Language] = Cache(CACHE_MAX_SIZE, CACHE_EXPIRY)

    def get_chat_language(self, chat_id: int) -> Language:
        """Returns the language a chat has chosen, or the bot's default when it has chosen none."""
        default = self._languages.default_language
        loaded = self._chat_cache.get(
            chat_id, lambda key: self._load_language(TYPE_CHAT, key), default
        )
        return loaded or default

    def get_user_language(self, user_id: int) -> Language:
        """Returns the language a user has chosen, or the bot's default when they have chosen
        none."""
        default = self._languages.default_language
        loaded = self._user_cache.get(
            user_id, lambda key: self._load_language(TYPE_USER, key), default
        )
        return loaded or default

    def set_chat_language(self, chat_id: int, language: Language) -> None:
        """Stores or replaces a chat's language preference."""
        self._store_language(TYPE_CHAT, chat_id, language)
        self._chat_cache.put(chat_id, language)

    def migrate_chat(self, from_chat_id: int, to_chat_id: int) -> None:
        """Moves a chat's language preference to a new chat id, for a group Telegram upgraded to a
        supergroup. Nothing moves when the old chat has no preference or the new chat already has
        one."""
        self._database.execute(
            "UPDATE language_preferences SET preference_id = ? "
            "WHERE preference_type = ? AND preference_id = ? AND NOT EXISTS "
            "(SELECT 1 FROM language_preferences WHERE preference_type = ? AND preference_id = ?)",
            (to_chat_id, TYPE_CHAT, from_chat_id, TYPE_CHAT, to_chat_id),
        )
        self._chat_cache.remove(from_chat_id)
        self._chat_cache.remove(to_chat_id)

    def set_user_language(self, user_id: int, language: Language) -> None:
        """Stores or replaces a user's language preference."""
        self._store_language(TYPE_USER, user_id, language)
        self._user_cache.put(user_id, language)

    def _store_language(self, type: str, id: int, language: Language) -> None:
        self._database.execute(
            "INSERT INTO language_preferences (preference_type, preference_id, language) "
            "VALUES (?, ?, ?) ON CONFLICT(preference_type, preference_id) "
            "DO UPDATE SET language = excluded.language",
            (type, id, language.code),
        )

    def _load_language(self, type: str, id: int) -> Language:
        try:
            stored = self._database.query_one(
                "SELECT language FROM language_preferences "
                "WHERE preference_type = ? AND preference_id = ?",
                (type, id),
                lambda row: self._languages.resolve_or_default(row["language"]),
            )
        except DatabaseException as e:
            log.warning("Failed to load %s language for %s: %s", type.lower(), id, e)
            raise LoadFailedException(f"Failed to load {type} language for {id}") from e

        return stored or self._languages.default_language
