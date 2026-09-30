"""The bot's own identity."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BotIdentity:
    """The bot's own identity as reported by Telegram at start-up.

    Handlers need it to recognise messages addressed to themselves (/start@botname), to ignore
    their own messages, and to build deep links, so it is resolved once and shared rather than
    looked up per update.

    Attributes:
        id: The bot's Telegram user id.
        username: The bot's username without the leading '@', or an empty string when it has none.
        name: The bot's display name.
    """

    id: int
    username: str
    name: str

    @property
    def has_username(self) -> bool:
        """Whether the bot has a username and can therefore be deep-linked."""
        return bool(self.username.strip())
