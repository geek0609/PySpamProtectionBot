"""The options the process was started with."""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from spb.exceptions import CommandLineException

DEFAULT_CONFIGURATION = "configuration.yml"
"""The configuration file used when --config is not given."""

DEFAULT_DATABASE = "./database.db"
"""The database file used when --database is not given."""

CONFIG_VARIABLE = "SPB_CONFIG"
DATABASE_VARIABLE = "SPB_DATABASE"


@dataclass(frozen=True, slots=True)
class CommandLineOptions:
    """The options the process was started with.

    Two resources have to be located before anything else can happen: the YAML configuration and
    the SQLite database. Both have a default, so the bot starts with no arguments at all in a
    directory that is laid out conventionally, and both can be pointed elsewhere for deployments
    that keep their state outside the working directory.

    Supported forms are -c <path>, --config <path>, --config=<path> and the same three for -d and
    --database. Anything else is rejected with the usage text rather than silently ignored.

    Each option can also be given as an environment variable, SPB_CONFIG and SPB_DATABASE, which
    suits containers and service managers. An option on the command line takes precedence over its
    variable, and the variable over the default. A variable that is set but empty is treated as
    unset.
    """

    configuration: Path
    database: Path
    help: bool

    @classmethod
    def parse(cls, args: Sequence[str], environment: Mapping[str, str] = os.environ) -> Self:
        """Reads the options out of the command line, falling back on the environment.

        Args:
            args: The command-line arguments, without the program name.
            environment: The environment variables to fall back on.

        Raises:
            CommandLineException: If an option is unknown, malformed, repeated or missing its value.
        """
        configuration: Path | None = None
        database: Path | None = None
        help = False

        index = 0
        while index < len(args):
            argument = args[index]
            index += 1

            if argument in ("-h", "--help"):
                help = True
                continue

            if not argument.startswith("-"):
                raise CommandLineException(f"Unexpected argument '{argument}'.\n{usage()}")

            name, equals, inline_value = argument.partition("=")
            if name not in ("-c", "--config", "-d", "--database"):
                raise CommandLineException(f"Unknown option '{name}'.\n{usage()}")

            if (configuration if name in ("-c", "--config") else database) is not None:
                raise CommandLineException(f"Option '{name}' was given more than once.\n{usage()}")

            if equals:
                value = inline_value
            elif index < len(args):
                value = args[index]
                index += 1
            else:
                value = ""

            if not value.strip():
                raise CommandLineException(f"Option '{name}' needs a value.\n{usage()}")

            if name in ("-c", "--config"):
                configuration = Path(value)
            else:
                database = Path(value)

        return cls(
            configuration=configuration
            or _from_environment(environment, CONFIG_VARIABLE)
            or Path(DEFAULT_CONFIGURATION),
            database=database
            or _from_environment(environment, DATABASE_VARIABLE)
            or Path(DEFAULT_DATABASE),
            help=help,
        )


def _from_environment(environment: Mapping[str, str], variable: str) -> Path | None:
    """Reads a path from an environment variable, None when it is unset or blank."""
    value = environment.get(variable, "").strip()
    return Path(value) if value else None


def usage() -> str:
    """Returns the usage text shown by --help and appended to parse errors."""
    return f"""\
SpamProtectionBot - Telegram spam protection backed by the Federated Database protocol

Usage:
  spb [options]

Options:
  -c, --config <path>     Path to the YAML configuration file (default: {DEFAULT_CONFIGURATION})
  -d, --database <path>   Path to the SQLite database file, created when missing
                          (default: {DEFAULT_DATABASE})
  -h, --help              Show this help and exit

Environment variables (used when the matching option is not given):
  {CONFIG_VARIABLE:<23} Same as --config
  {DATABASE_VARIABLE:<23} Same as --database
"""
