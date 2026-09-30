from pathlib import Path

from spb.classes.database import Database
from spb.classes.language_manager import LANGUAGE_DIRECTORY, LanguageManager
from spb.classes.managers.language_preference_manager import LanguagePreferenceManager
from tests.classes.managers.conftest import reopen

TEST_LANGUAGES = Path(__file__).parent.parent.parent / "resources" / "languages"
LANGUAGES = LanguageManager("en", (LANGUAGE_DIRECTORY, TEST_LANGUAGES))


def test_defaults_to_the_bot_language(database: Database) -> None:
    preferences = LanguagePreferenceManager(database, LANGUAGES)

    assert preferences.get_chat_language(-100) == LANGUAGES.default_language
    assert preferences.get_user_language(42) == LANGUAGES.default_language


def test_chat_and_user_preferences_are_independent(database: Database) -> None:
    fixture = LANGUAGES.resolve("zz")
    assert fixture is not None
    LanguagePreferenceManager(database, LANGUAGES).set_chat_language(-100, fixture)

    reopened = reopen(database)
    preferences = LanguagePreferenceManager(reopened, LANGUAGES)

    assert preferences.get_chat_language(-100) == fixture
    assert preferences.get_user_language(-100) == LANGUAGES.default_language
    reopened.close()


def test_preference_can_be_changed(database: Database) -> None:
    preferences = LanguagePreferenceManager(database, LANGUAGES)
    fixture = LANGUAGES.resolve("zz")
    assert fixture is not None

    preferences.set_user_language(42, fixture)
    preferences.set_user_language(42, LANGUAGES.default_language)

    assert preferences.get_user_language(42) == LANGUAGES.default_language


def test_migrates_a_chat(database: Database) -> None:
    preferences = LanguagePreferenceManager(database, LANGUAGES)
    fixture = LANGUAGES.resolve("zz")
    assert fixture is not None
    preferences.set_chat_language(-100, fixture)

    preferences.migrate_chat(-100, -1000000000100)

    assert preferences.get_chat_language(-1000000000100) == fixture
    assert preferences.get_chat_language(-100) == LANGUAGES.default_language
