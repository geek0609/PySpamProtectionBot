"""Enumerations used across the bot."""

from enum import Enum, auto
from typing import Self

from spb.classes.language_manager import LanguageManager
from spb.objects.language import Language
from spb.objects.update import Update


class UpdateType(Enum):
    """The kinds of update a handler can declare interest in.

    Each member knows how to recognise itself in an Update, so routing never needs a chain of checks
    on which field is set: a handler declares the types it serves through the update_handler
    decorator and the dispatcher matches them.

    ANY matches every update. COMMAND is a refinement of MESSAGE: it matches only text messages
    whose first token is a /command, so command handlers never have to parse the message themselves.
    Every other member is named after the Update field it matches.
    """

    ANY = "any"
    MESSAGE = "message"
    COMMAND = "command"
    CHANNEL_POST = "channel_post"
    CALLBACK_QUERY = "callback_query"
    MY_CHAT_MEMBER = "my_chat_member"
    CHAT_MEMBER = "chat_member"
    CHAT_JOIN_REQUEST = "chat_join_request"
    BUSINESS_CONNECTION = "business_connection"
    BUSINESS_MESSAGE = "business_message"
    EDITED_BUSINESS_MESSAGE = "edited_business_message"
    DELETED_BUSINESS_MESSAGES = "deleted_business_messages"

    def matches(self, update: Update) -> bool:
        """Returns whether the update is of this type."""
        if self is UpdateType.ANY:
            return True

        if self is UpdateType.COMMAND:
            return _is_command_message(update)

        return getattr(update, self.value) is not None

    @classmethod
    def of(cls, update: Update) -> Self | None:
        """Returns the most specific type of the update, used for diagnostic logging.

        COMMAND wins over MESSAGE for command messages; ANY is never returned as it is not a
        concrete kind of update.
        """
        return next(
            (
                member
                for member in cls
                if member not in (cls.ANY, cls.MESSAGE) and member.matches(update)
            ),
            cls.MESSAGE if cls.MESSAGE.matches(update) else None,
        )


def _is_command_message(update: Update) -> bool:
    """Returns whether the update carries a text message whose first token is a /command."""
    if update.message is None or update.message.text is None:
        return False

    trimmed = update.message.text.lstrip()
    return len(trimmed) > 1 and trimmed[0] == "/" and not trimmed[1].isspace()


class DispatchMode(Enum):
    """How a handler participates in dispatch once its declared update types match.

    The two modes exist so cross-cutting work (bookkeeping, scanning, membership tracking) can be
    expressed independently of the handler that ultimately answers the update, instead of every
    feature handler having to remember to call it.
    """

    OBSERVE = auto()
    """The handler runs for every matching update before any routing and never consumes it.

    All observers of an update run, in priority order, regardless of what any of them does. A
    failing observer is logged and skipped; it never prevents the remaining observers or the routed
    handler from running.
    """

    ROUTE = auto()
    """The handler competes to answer the update, and consumes it when selected.

    Routed handlers are evaluated in priority order and the first one that matches is the only one
    invoked, so a specific handler can claim an update ahead of a general one by declaring a higher
    priority.
    """


class ModerationAction(Enum):
    """The resolved moderation action to take (or not take) on a user."""

    NONE = auto()
    TEMPORARY_BAN = auto()
    PERMANENT_BAN = auto()
    TEMPORARY_RESTRICT = auto()
    PERMANENT_RESTRICT = auto()


class ScanningBehavior(Enum):
    """The moderation level applied to content and message-author user scanning results."""

    # Declared least to most strict; the settings menu lists the levels in this order.
    PASSIVE = auto()
    MODERATE = auto()
    STRICT = auto()

    @classmethod
    def from_string(cls, value: str | None) -> Self:
        """Parses a persisted moderation level, PASSIVE when unknown."""
        return cls.__members__.get(value or "", cls.PASSIVE)


class JoinProtectionBehavior(Enum):
    """The action taken when Federation recommends blocking a member who has just joined a chat."""

    # Declared least to most strict, and kept in the same order as ScanningBehavior: the settings
    # menu lists the levels in declaration order, so that order is what a moderator sees.
    # Persistence stores the name, never the position, so this order is safe to change.
    PASSIVE = auto()
    MODERATE = auto()
    STRICT = auto()

    @classmethod
    def from_string(cls, value: str | None) -> Self:
        """Parses a persisted protection behavior, PASSIVE when unknown."""
        return cls.__members__.get(value or "", cls.PASSIVE)


class SecretaryContactStatus(Enum):
    """The tracking status of a contact that wrote to a secretary-enabled business connection.

    Contacts start as UNKNOWN: the first message is checked through Federation. The owner can then
    approve or deny the contact; approved contacts are trusted and denied contacts are automatically
    handled without another notification.
    """

    UNKNOWN = auto()
    ALLOWED = auto()
    DENIED = auto()

    @classmethod
    def from_string(cls, value: str | None) -> Self:
        """Parses a persisted contact status, UNKNOWN when unknown."""
        return cls.__members__.get(value or "", cls.UNKNOWN)


class ReportOrigin(Enum):
    """Who asked for a report, which is all that distinguishes one submission from another.

    Several features end at the same Federation call: a member or administrator reporting a
    message, and an operator creating the report that backs a blacklist. They differ only in how
    the evidence is tagged and who hears about it afterwards, and both of those follow from which
    one it is.
    """

    MEMBER = ("user_report", True, True)
    """A chat member filed it; they get a summary and the moderators get a notification."""

    ADMIN = ("admin_report", True, False)
    """An administrator filed it with /report; they get a summary same as a member, but moderators
    are not separately notified since the reporter already is one."""

    OPERATOR_REFERENCE = ("admin_report", False, False)
    """An operator needs a report to blacklist against; created as a reference, told to nobody."""

    @property
    def evidence_tag(self) -> str:
        """The tag attached to the report's evidence."""
        return self.value[0]

    @property
    def is_summarised(self) -> bool:
        """Whether a summary goes back to whoever filed the report."""
        return self.value[1]

    @property
    def notifies_moderators(self) -> bool:
        """Whether the chat's moderators are notified of the report."""
        return self.value[2]


class ReportPage(Enum):
    """One of the pages of the two-step report dialog."""

    INCIDENT = auto()
    """The user is selecting an incident type."""

    COMMENT = auto()
    """The user may add a comment or submit the report."""


class ConfigurationPage(Enum):
    """One of the pages shown in the configure-settings view of the configuration menu."""

    MAIN = auto()
    SCANNING = auto()
    JOIN_PROTECTION = auto()
    REPORTING = auto()
    PRIVACY = auto()
    MODERATOR_NOTIFICATIONS = auto()
    CHANNEL = auto()
    LANGUAGE = auto()

    def previous(self) -> "ConfigurationPage":
        """Returns the previous settings page in cyclic order."""
        index = _ORDERED_PAGES.index(self) if self in _ORDERED_PAGES else 0
        return _ORDERED_PAGES[index - 1]

    def next(self) -> "ConfigurationPage":
        """Returns the next settings page in cyclic order."""
        index = _ORDERED_PAGES.index(self) if self in _ORDERED_PAGES else -1
        return _ORDERED_PAGES[(index + 1) % len(_ORDERED_PAGES)]


_ORDERED_PAGES = [
    ConfigurationPage.SCANNING,
    ConfigurationPage.JOIN_PROTECTION,
    ConfigurationPage.REPORTING,
    ConfigurationPage.PRIVACY,
    ConfigurationPage.MODERATOR_NOTIFICATIONS,
    ConfigurationPage.CHANNEL,
]


class HelpPage(Enum):
    """One page in the private /help guide."""

    MAIN = "main"
    SECRETARY_MODE = "secretary_mode"
    CHAT_PROTECTION = "chat_protection"
    SETTINGS = "settings"
    FEDERATION_COMMANDS = "federation_commands"
    OPERATORS = "operator_usage"

    def title(self, languages: LanguageManager, language: Language) -> str:
        """Returns the page's title in the given language."""
        return languages.get(language, "help_pages", f"{self.value}_title")

    def body(self, languages: LanguageManager, language: Language) -> str:
        """Returns the page's body in the given language."""
        return languages.get(language, "help_pages", f"{self.value}_body")


class SettingsPage(Enum):
    """One of the feature or setting sub-pages in the Settings help menu."""

    SCANNING = "scanning"
    JOIN_PROTECTION = "join_protection"
    REPORTING = "reporting"
    PRIVACY = "privacy"
    MODERATOR_NOTIFICATIONS = "moderator_notifications"
    CHANNEL_LINKING = "channel_linking"

    def title(self, languages: LanguageManager, language: Language) -> str:
        """Returns the page's title in the given language."""
        return languages.get(language, "help_pages", f"settings_{self.value}_title")

    def body(self, languages: LanguageManager, language: Language) -> str:
        """Returns the page's body in the given language."""
        return languages.get(language, "help_pages", f"settings_{self.value}_body")


class OperatorPage(Enum):
    """One of the command sub-pages in the Operator Usage help menu."""

    AUTHENTICATION = "authentication"
    BLACKLIST = "blacklist"
    LINKING = "linking"
    REPORT_ASSIGNMENTS = "report_assignments"

    def title(self, languages: LanguageManager, language: Language) -> str:
        """Returns the page's title in the given language."""
        return languages.get(language, "help_pages", f"operator_{self.value}_title")

    def body(self, languages: LanguageManager, language: Language) -> str:
        """Returns the page's body in the given language."""
        return languages.get(language, "help_pages", f"operator_{self.value}_body")
