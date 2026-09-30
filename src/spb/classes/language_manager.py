"""Translated user-facing strings, served from the bundled language files."""

import logging
from collections.abc import Iterable
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

import yaml

from spb.classes.configuration import UniqueKeyLoader
from spb.objects.language import Language

log = logging.getLogger(__name__)

LANGUAGE_DIRECTORY = files("spb") / "resources" / "languages"
"""The directory translation files are discovered in."""

RESOURCE_SUFFIX = ".yml"
PROPERTIES_SECTION = "localization_properties"
PROPERTY_NAME = "name"
PROPERTY_EMOJI = "emoji"


class LanguageManager:
    """Serves every user-facing string, translated, from the language files.

    Nothing the bot says to a user is written in code, and nothing in code knows which languages
    exist. At start-up this scans the language directory for <code>.yml files, reads each one's
    localization_properties section to learn what the language calls itself, and serves whatever
    it found. Adding a translation is adding one file; the settings menu, the language picker and
    this class all follow from it with no code change.

    A file is a nested YAML mapping flattened into dotted keys: the first segment is a section
    (general, configuration, ...) and nested mappings join with dots, so
    configuration.main_menu.enabled_header addresses a leaf at any depth. Templates carry positional
    placeholders, {0} and {1}, substituted at call time.

    A key missing from the requested language falls back to the default language, and a key
    missing everywhere returns its own dotted name, so a gap shows up as a visible key rather than
    an empty message.
    """

    def __init__(
        self, default_language_code: str, directories: Iterable[Traversable] = (LANGUAGE_DIRECTORY,)
    ) -> None:
        """
        Args:
            default_language_code: The ISO 639-1 code used when a key or a language is missing,
                typically bot.default_language from the configuration.
            directories: The directories translation files are discovered in.

        Raises:
            RuntimeError: If no translation file was found, or the default language has none, since
                the bot would then have nothing to say.
        """
        self._messages: dict[str, dict[str, str]] = {}
        self._by_code: dict[str, Language] = {}

        for directory in directories:
            for file in sorted(directory.iterdir(), key=lambda file: file.name):
                if file.is_file() and file.name.endswith(RESOURCE_SUFFIX):
                    self._load(file)

        if not self._by_code:
            raise RuntimeError("No translation files found; the bot would have nothing to say")

        self._available = sorted(self._by_code.values(), key=lambda language: language.code)

        default_language = self.resolve(default_language_code)
        if default_language is None:
            raise RuntimeError(
                f"No translation file for the default language '{default_language_code}'; "
                f"found {sorted(self._by_code)}"
            )

        self._default_language = default_language
        log.info(
            "Loaded %s language(s) %s (default: %s)",
            len(self._available),
            [language.code for language in self._available],
            default_language.code,
        )

    @property
    def default_language(self) -> Language:
        """The language used when a key or a requested language is missing."""
        return self._default_language

    @property
    def available_languages(self) -> list[Language]:
        """Every language that has a translation file, ordered by code.

        This is what the settings menu offers; there is no other list of languages anywhere.
        """
        return self._available

    def resolve(self, language_code: str | None) -> Language | None:
        """Returns the language for a code, or None when nothing matches.

        Accepts a regional variant, so a client reporting pt-BR resolves to pt when that file
        exists.
        """
        if language_code is None or not language_code.strip():
            return None

        exact = self._by_code.get(language_code.strip().lower())
        if exact is not None:
            return exact

        return next(
            (language for language in self._available if language.matches(language_code)), None
        )

    def resolve_or_default(self, language_code: str | None) -> Language:
        """Returns the language for a code, falling back to the default."""
        return self.resolve(language_code) or self._default_language

    def is_available(self, language: Language | None) -> bool:
        """Returns whether a language has a translation file."""
        return language is not None and language.code in self._by_code

    def get(self, language: Language | None, section: str, key: str, *args: object) -> str:
        """Returns a translated string with its placeholders filled in.

        Args:
            language: The language to translate into, or None for the default.
            section: The top-level section, such as general.
            key: The key within the section, nested levels joined with dots, such as
                main_menu.enabled_header.
            args: The values substituted for {0}, {1}, ... in order; without any, the raw template
                is returned.

        Returns:
            The translated string, or the dotted key when no translation exists.
        """
        result = self._lookup(language, section, key)
        for index, argument in enumerate(args):
            result = result.replace(f"{{{index}}}", str(argument))
        return result

    def key_count(self, language: Language | None) -> int:
        """Returns the number of translation keys loaded for a language."""
        return len(self._messages.get(language.code, {})) if language is not None else 0

    def _lookup(self, language: Language | None, section: str, key: str) -> str:
        """Finds a template, falling back to the default language and then to the key itself."""
        dotted = f"{section}.{key}"
        requested = language or self._default_language

        value = self._messages.get(requested.code, {}).get(dotted)
        if value is not None:
            return value

        if requested != self._default_language:
            value = self._messages[self._default_language.code].get(dotted)
            if value is not None:
                log.debug(
                    "Key '%s' is missing from '%s', serving '%s'",
                    dotted,
                    requested.code,
                    self._default_language.code,
                )
                return value

        log.warning("Missing translation key '%s'", dotted)
        return dotted

    def _load(self, file: Traversable) -> None:
        """Reads one translation file into the languages and messages being built.

        An unusable file (malformed, or not declaring what language it is) is logged and skipped
        rather than failing start-up, so one bad translation cannot stop the bot.
        """
        code = file.name.removesuffix(RESOURCE_SUFFIX)

        try:
            with file.open(encoding="utf-8") as stream:
                parsed = yaml.load(stream, Loader=UniqueKeyLoader)
        except (OSError, yaml.YAMLError) as e:
            log.warning("Failed to load translation file '%s': %s", file.name, e)
            return

        if not isinstance(parsed, dict):
            log.warning("Translation file '%s' is not a YAML mapping and was ignored", file.name)
            return

        flat: dict[str, str] = {}
        _flatten(None, parsed, flat)

        name = flat.get(f"{PROPERTIES_SECTION}.{PROPERTY_NAME}", "")
        emoji = flat.get(f"{PROPERTIES_SECTION}.{PROPERTY_EMOJI}", "")
        if not name.strip() or not emoji.strip():
            log.warning(
                "Translation file '%s' has no usable '%s' section ('%s' and '%s' are required) "
                "and was ignored",
                file.name,
                PROPERTIES_SECTION,
                PROPERTY_NAME,
                PROPERTY_EMOJI,
            )
            return

        language = Language(code, name, emoji)
        log.debug("Loaded %s translation key(s) for '%s' (%s %s)", len(flat), code, emoji, name)
        self._by_code[language.code] = language
        self._messages[language.code] = flat


def _flatten(prefix: str | None, node: Any, flat: dict[str, str]) -> None:
    """Flattens a nested YAML mapping into dotted keys, keeping only scalar leaves."""
    if isinstance(node, dict):
        for key, value in node.items():
            _flatten(str(key) if prefix is None else f"{prefix}.{key}", value, flat)
    elif node is not None and prefix is not None:
        flat[prefix] = str(node)
