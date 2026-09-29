"""Exceptions raised by the bot."""


class ArgumentParseException(Exception):
    """Raised when command arguments are invalid, carrying the user-facing message to send."""


class CommandLineException(Exception):
    """Raised when the command line cannot be parsed into a usable set of options."""


class ConfigurationException(Exception):
    """Raised when the configuration file cannot be read, parsed or validated."""


class DatabaseException(Exception):
    """Raised when the SQLite database cannot be opened, initialised or accessed."""


class FederationException(Exception):
    """Raised when a Federation operation cannot be completed.

    Covers both "no server is configured" and "the configured server refused or could not be
    reached". Handlers treat either the same way: tell the user the feature is unavailable and carry
    on, because Federation being down must never stop the bot from working.
    """


class HandlerException(Exception):
    """Raised when the handler layer cannot be discovered or instantiated.

    This signals a programming error in a handler declaration rather than a failure while
    processing an update, which the dispatcher logs and swallows instead.
    """
