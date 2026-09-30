"""Tests that every text the bot shows has a translation."""

import re
from pathlib import Path

import pytest

import spb
from spb.classes.language_manager import LanguageManager
from spb.enums import HelpPage, SettingsPage

LOOKUP = re.compile(r'\bget\(\s*[^,()]+,\s*"([a-z0-9_]+)"\s*,\s*"([a-z0-9_.]+)"\s*(?=[,)])')
"""get(..., "section", "key", ...) with the section and key written out."""

SOURCE_ROOT = Path(spb.__file__).parent
LANGUAGE_FILES = sorted((SOURCE_ROOT / "resources" / "languages").glob("*.yml"))


@pytest.fixture(scope="module")
def languages() -> LanguageManager:
    return LanguageManager("en")


def is_translated(languages: LanguageManager, section: str, key: str) -> bool:
    """Whether the manager has a real translation for the key rather than its own name."""
    return languages.get(languages.default_language, section, key) != f"{section}.{key}"


def test_every_translation_key_used_in_the_source_exists_in_en_yml(
    languages: LanguageManager,
) -> None:
    missing = set()
    checked = 0

    for source in SOURCE_ROOT.rglob("*.py"):
        for match in LOOKUP.finditer(source.read_text(encoding="utf-8")):
            checked += 1
            if not is_translated(languages, match[1], match[2]):
                missing.add(f"{match[1]}.{match[2]}  ({source.name})")

    assert checked > 200, f"expected the scan to find the translation calls, found {checked}"
    assert missing == set(), "translation keys used in code but missing from en.yml"


def test_every_help_and_settings_page_has_a_title_and_a_body(languages: LanguageManager) -> None:
    lang = languages.default_language
    missing = set()

    for page in [*HelpPage, *SettingsPage]:
        for text in (page.title(languages, lang), page.body(languages, lang)):
            if text.startswith("help_pages."):
                missing.add(text)

    assert missing == set(), "help or settings pages without translated text"


def test_every_discovered_language_declares_how_to_show_itself(languages: LanguageManager) -> None:
    # A file that omits localization_properties is skipped rather than offered, so anything
    # reaching the settings menu can be drawn as a button.
    for language in languages.available_languages:
        assert language.name.strip(), f"{language.code} has no name"
        assert language.emoji.strip(), f"{language.code} has no emoji"
        assert language.label.strip(), f"{language.code} has no button label"


def test_every_language_file_declares_how_it_wants_to_be_shown() -> None:
    # A file without localization_properties is skipped rather than offered, so a translation that
    # forgets the section would silently never appear in the picker.
    undeclared = {
        file.name
        for file in LANGUAGE_FILES
        if "localization_properties:" not in file.read_text(encoding="utf-8")
    }

    assert undeclared == set(), "language files with no localization_properties section"


def test_every_shipped_language_file_is_actually_discovered(languages: LanguageManager) -> None:
    on_disk = {file.stem for file in LANGUAGE_FILES}
    discovered = {language.code for language in languages.available_languages}

    assert on_disk - discovered == set(), "shipped but not discovered"


def test_no_user_facing_text_is_hard_coded_in_the_handlers() -> None:
    # A handler sending a bare sentence instead of a lookup is how a translation gap gets
    # introduced, so the shape is checked rather than trusted. Known-safe literals (markup,
    # callback data, separators, formats) are allowed through.
    sentence = re.compile(r'\btext="([^"]{15,})"')
    offenders = [
        f"{source.name}: {match[1]}"
        for source in (SOURCE_ROOT / "handlers").rglob("*.py")
        for match in sentence.finditer(source.read_text(encoding="utf-8"))
    ]

    assert offenders == [], "user-facing text sent without going through the language manager"
