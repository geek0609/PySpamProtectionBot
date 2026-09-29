"""The process configuration, loaded and validated from a YAML file."""

import os
import re
from collections.abc import Hashable
from os import PathLike
from pathlib import Path
from typing import Any, Literal, overload
from urllib.parse import SplitResult, urlsplit

import yaml

from spb.exceptions import ConfigurationException

DEFAULT_QUEUE_CAPACITY = 1024
DEFAULT_NOTIFICATION_INTERVAL_SECONDS = 60
DEFAULT_LANGUAGE = "en"
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 30

# Every character RFC 3986 allows in a URL, so a value with anything else is rejected as malformed
# rather than silently percent-encoded.
_URL_CHARACTERS = re.compile(r"[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*")


class UniqueKeyLoader(yaml.SafeLoader):
    """A safe YAML loader that rejects duplicate mapping keys instead of keeping the last one."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            # The base constructor reports an unhashable key itself.
            if not isinstance(key, Hashable):
                continue
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


class Configuration:
    """The process configuration, loaded and validated from a YAML file.

    Constructing an instance reads the file with a safe YAML loader and validates it field by field;
    every failure is reported as a ConfigurationException naming the offending section and key, so
    operators get immediate, actionable feedback instead of silent misconfiguration. The instance is
    treated as read-only for the lifetime of the process.

    The bot section is required. The federation section is optional; when it is absent,
    has_federation is False and the bot runs without Federation support.
    """

    def __init__(self, file_path: str | PathLike[str]) -> None:
        """
        Args:
            file_path: The configuration file path.

        Raises:
            ConfigurationException: If the file is missing, unreadable, malformed or invalid.
        """
        self.path = Path(file_path)

        root = _read(self.path)
        bot = _get_map(root, "bot", optional=False)
        federation = _get_map(root, "federation", optional=True)

        self.name = _require_text(_get_string(bot, "name", optional=False), "bot.name")
        self.api_key = _require_text(_get_string(bot, "api_key", optional=False), "bot.api_key")
        self.api_id = _get_integer(bot, "api_id", optional=False)
        self.api_hash = _require_text(_get_string(bot, "api_hash", optional=False), "bot.api_hash")
        self.test_server = _or_default(_get_boolean(bot, "test_server"), False)
        self.worker_threads = _or_default(
            _get_integer(bot, "worker_threads", optional=True), _default_worker_threads()
        )
        self.queue_capacity = _or_default(
            _get_integer(bot, "queue_capacity", optional=True), DEFAULT_QUEUE_CAPACITY
        )
        self.notification_interval_seconds = _or_default(
            _get_integer(bot, "notification_interval_seconds", optional=True),
            DEFAULT_NOTIFICATION_INTERVAL_SECONDS,
        )
        self.privacy_mode = _or_default(_get_boolean(bot, "privacy_mode"), False)
        self.default_language = _or_default(
            _get_string(bot, "default_language", optional=True), DEFAULT_LANGUAGE
        )
        self.shutdown_timeout_seconds = _or_default(
            _get_integer(bot, "shutdown_timeout_seconds", optional=True),
            DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
        )

        self.federation_endpoint: str | None = None
        self.federation_access_token: str | None = None
        self.federation_web_application_endpoint: str | None = None
        if federation is not None:
            self.federation_endpoint = _require_text(
                _get_string(federation, "endpoint", optional=False), "federation.endpoint"
            )
            self.federation_access_token = _get_string(federation, "access_token", optional=True)
            self.federation_web_application_endpoint = _get_string(
                federation, "web_application_endpoint", optional=True
            )

        self._validate()

    @property
    def has_federation(self) -> bool:
        """Whether the optional federation section is present, so the bot runs with Federation."""
        return self.federation_endpoint is not None

    def _validate(self) -> None:
        """Validates every field that carries a constraint beyond its type."""
        for field, value in (
            ("api_id", self.api_id),
            ("worker_threads", self.worker_threads),
            ("queue_capacity", self.queue_capacity),
            ("notification_interval_seconds", self.notification_interval_seconds),
            ("shutdown_timeout_seconds", self.shutdown_timeout_seconds),
        ):
            if value < 1:
                raise ConfigurationException(f"Field 'bot.{field}' must be >= 1, got {value}")

        if self.federation_endpoint is not None:
            _validate_endpoint(self.federation_endpoint)

        if self.federation_web_application_endpoint is not None:
            _validate_web_application_endpoint(self.federation_web_application_endpoint)

        if self.federation_access_token is not None and any(
            map(str.isspace, self.federation_access_token)
        ):
            raise ConfigurationException(
                "Field 'federation.access_token' must not contain whitespace"
            )


def _read(path: Path) -> dict[str, Any]:
    """Reads and parses the YAML file at the given path into its root mapping."""
    if not path.exists():
        raise ConfigurationException(f"Configuration file does not exist: {path}")

    try:
        with path.open(encoding="utf-8") as stream:
            parsed = yaml.load(stream, Loader=UniqueKeyLoader)
    except OSError as e:
        raise ConfigurationException(f"Failed to read configuration file {path}: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigurationException(f"Malformed YAML in configuration file {path}: {e}") from e

    if parsed is None:
        raise ConfigurationException(f"Configuration file is empty: {path}")

    if not isinstance(parsed, dict):
        raise ConfigurationException(
            f"Configuration file must contain a YAML mapping at the root: {path}"
        )

    return _cast_string_map(parsed, "root")


def _parse_url(value: str, field: str) -> SplitResult:
    """Splits a URL into its components, rejecting one that is not a valid URL."""
    try:
        if not _URL_CHARACTERS.fullmatch(value):
            raise ValueError("illegal character")
        return urlsplit(value)
    except ValueError as e:
        raise ConfigurationException(f"Field '{field}' must be a valid URL, got {value}") from e


def _validate_endpoint(endpoint: str) -> None:
    """Verifies that the Federation endpoint is a valid URL with a host.

    This mirrors the validation the PyFederation client performs on construction, so
    misconfiguration is caught at load time.
    """
    if not _parse_url(endpoint, "federation.endpoint").hostname:
        raise ConfigurationException(
            f"Field 'federation.endpoint' must have a valid host, got {endpoint}"
        )


def _validate_web_application_endpoint(endpoint: str) -> None:
    """Verifies that the Federation Web Application endpoint is an absolute http(s) URL with a host.

    Telegram rejects any other URL on an inline button and would then refuse the whole message
    carrying it.
    """
    field = "federation.web_application_endpoint"
    url = _parse_url(endpoint, field)

    if url.scheme.lower() not in ("https", "http"):
        raise ConfigurationException(
            f"Field '{field}' must be an http or https URL, got {endpoint}"
        )

    if not url.hostname:
        raise ConfigurationException(f"Field '{field}' must have a valid host, got {endpoint}")

    if "?" in endpoint or "#" in endpoint:
        raise ConfigurationException(
            f"Field '{field}' must not contain a query or fragment, got {endpoint}"
        )


def _default_worker_threads() -> int:
    """Returns the worker count used when bot.worker_threads is not configured."""
    return max(2, os.cpu_count() or 0)


def _require_text(value: str | None, field: str) -> str:
    """Rejects a required value that is blank."""
    if value is None or not value.strip():
        raise ConfigurationException(f"Field '{field}' must not be blank")

    return value


def _or_default[T](value: T | None, fallback: T) -> T:
    """Returns the value, or the default when the value is absent."""
    return value if value is not None else fallback


@overload
def _get_string(section: dict[str, Any], key: str, *, optional: Literal[False]) -> str: ...
@overload
def _get_string(section: dict[str, Any], key: str, *, optional: Literal[True]) -> str | None: ...
def _get_string(section: dict[str, Any], key: str, *, optional: bool) -> str | None:
    """Extracts a string field from a YAML mapping, None when optional and absent or empty."""
    value = section.get(key)
    if value is None:
        if optional:
            return None
        raise ConfigurationException(f"Missing required field '{key}'")

    if not isinstance(value, str):
        raise ConfigurationException(f"Field '{key}' must be a string, got {_type_name(value)}")

    if not value:
        if optional:
            return None
        raise ConfigurationException(f"Field '{key}' must not be empty")

    return value


@overload
def _get_integer(section: dict[str, Any], key: str, *, optional: Literal[False]) -> int: ...
@overload
def _get_integer(section: dict[str, Any], key: str, *, optional: Literal[True]) -> int | None: ...
def _get_integer(section: dict[str, Any], key: str, *, optional: bool) -> int | None:
    """Extracts an integer field from a YAML mapping, None when optional and absent."""
    value = section.get(key)
    if value is None:
        if optional:
            return None
        raise ConfigurationException(f"Missing required field '{key}'")

    # bool is a subclass of int, but a YAML boolean is never an acceptable integer.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigurationException(f"Field '{key}' must be an integer, got {_type_name(value)}")

    return value


def _get_boolean(section: dict[str, Any], key: str) -> bool | None:
    """Extracts an optional boolean field from a YAML mapping, None when absent."""
    value = section.get(key)
    if value is None:
        return None

    if not isinstance(value, bool):
        raise ConfigurationException(f"Field '{key}' must be a boolean, got {_type_name(value)}")

    return value


@overload
def _get_map(section: dict[str, Any], key: str, *, optional: Literal[False]) -> dict[str, Any]: ...
@overload
def _get_map(
    section: dict[str, Any], key: str, *, optional: Literal[True]
) -> dict[str, Any] | None: ...
def _get_map(section: dict[str, Any], key: str, *, optional: bool) -> dict[str, Any] | None:
    """Extracts a nested mapping from a YAML mapping, None when optional and absent."""
    value = section.get(key)
    if value is None:
        if optional:
            return None
        raise ConfigurationException(f"Missing required section '{key}'")

    return _cast_string_map(value, key)


def _cast_string_map(value: Any, name: str) -> dict[str, Any]:
    """Checks that a parsed YAML object is a mapping with string keys."""
    if not isinstance(value, dict):
        raise ConfigurationException(f"Section '{name}' must be a mapping, got {_type_name(value)}")

    for key in value:
        if not isinstance(key, str):
            raise ConfigurationException(
                f"Section '{name}' must only contain string keys, got {_type_name(key)}"
            )

    return value


def _type_name(value: Any) -> str:
    """Returns a human-readable type name for error messages."""
    if isinstance(value, dict):
        return "a mapping"
    if isinstance(value, list):
        return "a list"
    return f"a {type(value).__name__}"
