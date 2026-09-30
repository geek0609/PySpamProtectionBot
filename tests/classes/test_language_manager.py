from pathlib import Path

import pytest

from spb.classes.language_manager import LANGUAGE_DIRECTORY, LanguageManager
from spb.objects.language import Language

TEST_LANGUAGES = Path(__file__).parent.parent / "resources" / "languages"


def new_manager(default_language_code: str = "en") -> LanguageManager:
    return LanguageManager(default_language_code, (LANGUAGE_DIRECTORY, TEST_LANGUAGES))


@pytest.fixture
def languages() -> LanguageManager:
    return new_manager()


def resolve(languages: LanguageManager, code: str) -> Language:
    language = languages.resolve(code)
    assert language is not None
    return language


# Discovering languages


def test_discovers_undeclared_language(languages: LanguageManager) -> None:
    fixture = resolve(languages, "zz")

    assert fixture.code == "zz"
    assert fixture.name == "Zzyzx"  # the name comes from localization_properties
    assert fixture.emoji == "🧪"  # the emoji comes from localization_properties


def test_offers_every_discovered_language(languages: LanguageManager) -> None:
    codes = [language.code for language in languages.available_languages]

    # Not pinned to an exact list: shipped translations come and go without this test needing to
    # know about them.
    assert codes == sorted(set(codes))
    assert "en" in codes
    assert "zz" in codes
    assert all(languages.resolve(code) is not None for code in codes)


def test_unknown_languages_are_absent(languages: LanguageManager) -> None:
    assert languages.resolve("qq") is None
    assert not languages.is_available(Language("qq", "Nope", "?"))


def test_loads_english(languages: LanguageManager) -> None:
    english = resolve(languages, "en")

    assert languages.is_available(english)
    assert languages.key_count(english) > 400


def test_refuses_untranslated_default() -> None:
    with pytest.raises(RuntimeError, match="qq"):
        new_manager("qq")


def test_reports_the_default(languages: LanguageManager) -> None:
    assert languages.default_language.code == "en"
    assert new_manager("zz").default_language.code == "zz"


# Resolving codes


def test_resolves_regional_variants(languages: LanguageManager) -> None:
    assert resolve(languages, "en-GB").code == "en"
    assert resolve(languages, "EN").code == "en"


def test_falls_back_to_default(languages: LanguageManager) -> None:
    assert languages.resolve_or_default("qq").code == "en"
    assert languages.resolve_or_default(None).code == "en"
    assert languages.resolve_or_default("  ").code == "en"


def test_resolves_nothing_without_fallback(languages: LanguageManager) -> None:
    assert languages.resolve("qq") is None
    assert languages.resolve(None) is None


# Serving translations


def test_resolves_top_level_key(languages: LanguageManager) -> None:
    english = resolve(languages, "en")

    assert languages.get(english, "general", "yes") == "Yes"
    assert languages.get(english, "general", "cancel") == "Cancel"


def test_each_language_serves_its_own(languages: LanguageManager) -> None:
    assert languages.get(resolve(languages, "zz"), "general", "yes") == "Aye"
    assert languages.get(resolve(languages, "en"), "general", "yes") == "Yes"


def test_falls_back_per_key(languages: LanguageManager) -> None:
    # The fixture translates two keys; everything else must come from English rather than showing
    # a raw key to a user who chose that language.
    english = languages.get(resolve(languages, "en"), "general", "dismiss")

    assert languages.get(resolve(languages, "zz"), "general", "dismiss") == english


def test_resolves_nested_key(languages: LanguageManager) -> None:
    english = resolve(languages, "en")

    assert (
        languages.get(english, "general", "error.occurred")
        == "An error occurred. Please try again later."
    )
    assert languages.get(english, "configuration", "scanning.behavior_passive") == "Passive"


def test_missing_key_returns_its_name(languages: LanguageManager) -> None:
    assert (
        languages.get(resolve(languages, "en"), "general", "no_such_key") == "general.no_such_key"
    )


def test_no_language_is_served_the_default(languages: LanguageManager) -> None:
    assert languages.get(None, "general", "yes") == "Yes"


def test_substitutes_in_order(languages: LanguageManager) -> None:
    text = languages.get(resolve(languages, "en"), "general", "language.current", "English", "🇺🇸")

    assert text == "Current language: English 🇺🇸"


def test_raw_keeps_placeholders(languages: LanguageManager) -> None:
    english = resolve(languages, "en")
    raw = languages.get(english, "general", "language.current")

    assert "{0}" in raw
    assert raw != languages.get(english, "general", "language.current", "English", "flag")


# Describing a language


def test_builds_button_label(languages: LanguageManager) -> None:
    assert resolve(languages, "zz").label == "🧪 Zzyzx"


def test_refuses_unusable_language() -> None:
    with pytest.raises(ValueError, match="code must not be blank"):
        Language("  ", "Name", "x")
    with pytest.raises(ValueError, match="name must not be blank"):
        Language("xx", "  ", "x")


def test_matches_codes(languages: LanguageManager) -> None:
    english = resolve(languages, "en")

    assert english.matches("en")
    assert english.matches("EN")
    assert english.matches("en-US")
    assert not english.matches("de")
    assert not english.matches(None)
