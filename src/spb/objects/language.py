"""A language the bot can speak."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Language:
    """A language the bot can speak, as declared by its own translation file.

    Languages are not a fixed list in code. Each file under languages/ names itself with an
    ISO 639-1 code and declares how it wants to be shown in its own localization_properties
    section, so adding a translation is adding one file and restarting.

    Attributes:
        code: The ISO 639-1 code, lowercase, taken from the file name.
        name: The language's name written in that language, for the settings menu.
        emoji: The flag or other emoji shown beside the name.
    """

    code: str
    name: str
    emoji: str

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("code must not be blank")

        if not self.name.strip():
            raise ValueError(f"name must not be blank for language '{self.code}'")

        object.__setattr__(self, "code", self.code.lower())

    @property
    def label(self) -> str:
        """The label shown on the language's button, emoji first."""
        return f"{self.emoji} {self.name}"

    def matches(self, language_code: str | None) -> bool:
        """Returns whether this language answers to the given code.

        Matching accepts a regional variant: a client reporting pt-BR is served by pt when that is
        what exists.
        """
        if language_code is None or not language_code.strip():
            return False

        normalized = language_code.strip().lower()
        if self.code == normalized:
            return True

        region = normalized.find("-")
        return region > 0 and self.code == normalized[:region]
