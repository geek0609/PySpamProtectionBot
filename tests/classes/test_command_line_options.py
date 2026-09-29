from pathlib import Path

import pytest

from spb.classes.command_line_options import (
    CONFIG_VARIABLE,
    DATABASE_VARIABLE,
    DEFAULT_CONFIGURATION,
    DEFAULT_DATABASE,
    CommandLineOptions,
    usage,
)
from spb.exceptions import CommandLineException

# Every case passes its environment explicitly, so a variable set on the machine running the tests
# cannot change their outcome.


def parse(*args: str) -> CommandLineOptions:
    return CommandLineOptions.parse(args, {})


def failure(*args: str) -> str:
    with pytest.raises(CommandLineException) as failure:
        CommandLineOptions.parse(args, {})
    return str(failure.value)


# Applying defaults


def test_uses_defaults() -> None:
    options = parse()

    assert options.configuration == Path("configuration.yml")
    assert options.database == Path("./database.db")
    assert not options.help


# Reading options


def test_reads_long_form() -> None:
    options = parse(
        "--config", "/etc/spb/configuration.yml", "--database", "/var/lib/spb/database.db"
    )

    assert options.configuration == Path("/etc/spb/configuration.yml")
    assert options.database == Path("/var/lib/spb/database.db")


def test_reads_short_form() -> None:
    options = parse("-c", "custom.yml", "-d", "custom.db")

    assert options.configuration == Path("custom.yml")
    assert options.database == Path("custom.db")


def test_reads_inline_value() -> None:
    options = parse("--config=inline.yml", "-d=inline.db")

    assert options.configuration == Path("inline.yml")
    assert options.database == Path("inline.db")


def test_order_does_not_matter() -> None:
    options = parse("-d", "state.db", "--config", "settings.yml")

    assert options.configuration == Path("settings.yml")
    assert options.database == Path("state.db")


def test_reads_help() -> None:
    assert parse("--help").help
    assert parse("-h").help
    assert parse("--config", "a.yml", "--help").help


# Rejecting bad arguments


def test_rejects_unknown_option() -> None:
    message = failure("--verbose")

    assert "--verbose" in message
    assert "Usage:" in message


def test_rejects_positional_argument() -> None:
    assert "configuration.yml" in failure("configuration.yml")


def test_rejects_missing_value() -> None:
    assert "needs a value" in failure("--config")
    assert "needs a value" in failure("-d")
    assert "needs a value" in failure("--config=")


def test_rejects_repeated_option() -> None:
    assert "more than once" in failure("-c", "a.yml", "--config", "b.yml")
    assert "more than once" in failure("-d", "a.db", "-d", "b.db")


# Reading the environment


def test_reads_variables() -> None:
    options = CommandLineOptions.parse(
        [],
        {
            CONFIG_VARIABLE: "/etc/spb/configuration.yml",
            DATABASE_VARIABLE: "/var/lib/spb/database.db",
        },
    )

    assert options.configuration == Path("/etc/spb/configuration.yml")
    assert options.database == Path("/var/lib/spb/database.db")


def test_command_line_wins() -> None:
    options = CommandLineOptions.parse(
        ["--config", "cli.yml"], {CONFIG_VARIABLE: "env.yml", DATABASE_VARIABLE: "env.db"}
    )

    assert options.configuration == Path("cli.yml")
    assert options.database == Path("env.db")


def test_blank_is_unset() -> None:
    options = CommandLineOptions.parse([], {CONFIG_VARIABLE: "", DATABASE_VARIABLE: "   "})

    assert options.configuration == Path(DEFAULT_CONFIGURATION)
    assert options.database == Path(DEFAULT_DATABASE)


# Describing itself


def test_usage_is_complete() -> None:
    text = usage()

    assert "--config" in text
    assert "--database" in text
    assert "--help" in text
    assert DEFAULT_CONFIGURATION in text
    assert DEFAULT_DATABASE in text
    assert CONFIG_VARIABLE in text
    assert DATABASE_VARIABLE in text
