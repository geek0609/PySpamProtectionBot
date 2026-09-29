import os
from pathlib import Path

import pytest

from spb.classes.configuration import Configuration
from spb.exceptions import ConfigurationException

BOT = """
bot:
  name: SpamProtectionBot
  api_key: "123456:ABCDEF"
  api_id: 12345
  api_hash: "0123456789abcdef0123456789abcdef"
"""


def write(directory: Path, content: str) -> Path:
    path = directory / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def load(directory: Path, content: str) -> Configuration:
    return Configuration(write(directory, content))


def load_failure(directory: Path, content: str) -> str:
    path = write(directory, content)
    with pytest.raises(ConfigurationException) as failure:
        Configuration(path)
    return str(failure.value)


# Loading valid configurations


def test_loads_every_field(tmp_path: Path) -> None:
    configuration = load(
        tmp_path,
        """
bot:
  name: SpamProtectionBot
  api_key: "123456:ABCDEF"
  api_id: 12345
  api_hash: "0123456789abcdef0123456789abcdef"
  test_server: true
  worker_threads: 8
  queue_capacity: 2048
  notification_interval_seconds: 30
  default_language: es
  privacy_mode: true
  shutdown_timeout_seconds: 10
federation:
  endpoint: "http://127.0.0.1:7000/"
  access_token: "abcdefghijklmnopqrstuvwxyz123456"
  web_application_endpoint: "https://federation.example.com/"
""",
    )

    assert configuration.name == "SpamProtectionBot"
    assert configuration.api_key == "123456:ABCDEF"
    assert configuration.api_id == 12345
    assert configuration.api_hash == "0123456789abcdef0123456789abcdef"
    assert configuration.test_server
    assert configuration.worker_threads == 8
    assert configuration.queue_capacity == 2048
    assert configuration.notification_interval_seconds == 30
    assert configuration.default_language == "es"
    assert configuration.privacy_mode
    assert configuration.shutdown_timeout_seconds == 10
    assert configuration.has_federation
    assert configuration.federation_endpoint == "http://127.0.0.1:7000/"
    assert configuration.federation_access_token == "abcdefghijklmnopqrstuvwxyz123456"
    assert configuration.federation_web_application_endpoint == "https://federation.example.com/"


def test_applies_defaults(tmp_path: Path) -> None:
    configuration = load(tmp_path, BOT)

    assert not configuration.test_server
    assert configuration.worker_threads == max(2, os.cpu_count() or 0)
    assert configuration.queue_capacity == 1024
    assert configuration.notification_interval_seconds == 60
    assert not configuration.privacy_mode
    assert configuration.default_language == "en"
    assert configuration.shutdown_timeout_seconds == 30


def test_federation_is_optional(tmp_path: Path) -> None:
    configuration = load(tmp_path, BOT)

    assert not configuration.has_federation
    assert configuration.federation_endpoint is None
    assert configuration.federation_access_token is None
    assert configuration.federation_web_application_endpoint is None


def test_federation_access_token_is_optional(tmp_path: Path) -> None:
    configuration = load(
        tmp_path,
        BOT + 'federation:\n  endpoint: "https://federation.example.com/"\n',
    )

    assert configuration.has_federation
    assert configuration.federation_endpoint == "https://federation.example.com/"
    assert configuration.federation_access_token is None


def test_accepts_string_path(tmp_path: Path) -> None:
    path = write(tmp_path, BOT)

    configuration = Configuration(str(path))

    assert configuration.name == "SpamProtectionBot"
    assert configuration.path == path


def test_ignores_comments(tmp_path: Path) -> None:
    configuration = load(
        tmp_path,
        """
# SpamProtectionBot configuration

bot:
  # required
  name: SpamProtectionBot
  api_key: "123456:ABCDEF"  # issued by BotFather
  api_id: 12345
  api_hash: "0123456789abcdef0123456789abcdef"
""",
    )

    assert configuration.name == "SpamProtectionBot"


def test_ignores_unknown_keys(tmp_path: Path) -> None:
    configuration = load(
        tmp_path,
        BOT + "  api_host: api.telegram.org\n  read_timeout_seconds: 300\n",
    )

    assert configuration.name == "SpamProtectionBot"


# Rejecting unusable files


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationException, match="does not exist"):
        Configuration(tmp_path / "absent.yaml")


def test_rejects_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationException):
        Configuration(tmp_path)


def test_rejects_empty_file(tmp_path: Path) -> None:
    assert "empty" in load_failure(tmp_path, "")


def test_rejects_comment_only_file(tmp_path: Path) -> None:
    assert "empty" in load_failure(tmp_path, "# nothing here\n")


def test_rejects_malformed_yaml(tmp_path: Path) -> None:
    assert "Malformed YAML" in load_failure(tmp_path, "bot:\n  name: [unterminated\n")


def test_rejects_scalar_root(tmp_path: Path) -> None:
    assert "mapping at the root" in load_failure(tmp_path, "just a string\n")


def test_rejects_duplicate_keys(tmp_path: Path) -> None:
    message = load_failure(tmp_path, BOT + "  name: Impostor\n")

    assert "Malformed YAML" in message


# Validating the bot section


def test_requires_bot_section(tmp_path: Path) -> None:
    message = load_failure(tmp_path, 'federation:\n  endpoint: "http://127.0.0.1:7000/"\n')

    assert "Missing required section 'bot'" in message


def test_requires_bot_mapping(tmp_path: Path) -> None:
    assert "Section 'bot' must be a mapping" in load_failure(tmp_path, "bot: SpamProtectionBot\n")


def test_requires_name(tmp_path: Path) -> None:
    assert "'name'" in load_failure(tmp_path, BOT.replace("  name: SpamProtectionBot\n", ""))


def test_rejects_blank_name(tmp_path: Path) -> None:
    message = load_failure(tmp_path, BOT.replace("name: SpamProtectionBot", 'name: "   "'))

    assert "bot.name" in message


def test_requires_api_key(tmp_path: Path) -> None:
    assert "'api_key'" in load_failure(tmp_path, BOT.replace('  api_key: "123456:ABCDEF"\n', ""))


def test_rejects_empty_api_key(tmp_path: Path) -> None:
    message = load_failure(tmp_path, BOT.replace('api_key: "123456:ABCDEF"', 'api_key: ""'))

    assert "api_key" in message


def test_requires_api_id(tmp_path: Path) -> None:
    assert "'api_id'" in load_failure(tmp_path, BOT.replace("  api_id: 12345\n", ""))


def test_requires_api_hash(tmp_path: Path) -> None:
    message = load_failure(
        tmp_path, BOT.replace('  api_hash: "0123456789abcdef0123456789abcdef"\n', "")
    )

    assert "'api_hash'" in message


def test_rejects_non_string_name(tmp_path: Path) -> None:
    message = load_failure(tmp_path, BOT.replace("name: SpamProtectionBot", "name: 42"))

    assert "must be a string" in message


def test_rejects_non_integer_api_id(tmp_path: Path) -> None:
    message = load_failure(tmp_path, BOT.replace("api_id: 12345", 'api_id: "12345"'))

    assert "must be an integer" in message


def test_rejects_boolean_integer(tmp_path: Path) -> None:
    message = load_failure(tmp_path, BOT + "  worker_threads: true\n")

    assert "must be an integer" in message


def test_rejects_non_boolean_test_server(tmp_path: Path) -> None:
    message = load_failure(tmp_path, BOT + "  test_server: yes-please\n")

    assert "must be a boolean" in message


def test_rejects_schemeless_web_application_endpoint(tmp_path: Path) -> None:
    # Telegram refuses a message whose button URL it cannot open, so accepting this would silently
    # break every message that displays a Federation record.
    message = load_failure(
        tmp_path,
        BOT
        + 'federation:\n  endpoint: "https://federation.example.com/"\n'
        + '  web_application_endpoint: "federation.example.com"\n',
    )

    assert "web_application_endpoint" in message


def test_rejects_web_application_endpoint_with_query(tmp_path: Path) -> None:
    message = load_failure(
        tmp_path,
        BOT
        + 'federation:\n  endpoint: "https://federation.example.com/"\n'
        + '  web_application_endpoint: "https://federation.example.com/?page=1"\n',
    )

    assert "query or fragment" in message


@pytest.mark.parametrize(
    "field",
    [
        "api_id",
        "worker_threads",
        "queue_capacity",
        "notification_interval_seconds",
        "shutdown_timeout_seconds",
    ],
)
def test_rejects_non_positive_values(tmp_path: Path, field: str) -> None:
    content = BOT.replace("  api_id: 12345\n", "") + f"  {field}: 0\n"
    if field != "api_id":
        content += "  api_id: 12345\n"

    assert field in load_failure(tmp_path, content)


def test_empty_optional_falls_back_to_default(tmp_path: Path) -> None:
    configuration = load(tmp_path, BOT + '  default_language: ""\n')

    assert configuration.default_language == "en"


# Validating the federation section


def test_requires_endpoint(tmp_path: Path) -> None:
    message = load_failure(tmp_path, BOT + 'federation:\n  access_token: "abcdef"\n')

    assert "'endpoint'" in message


def test_requires_federation_mapping(tmp_path: Path) -> None:
    message = load_failure(tmp_path, BOT + 'federation: "http://127.0.0.1:7000/"\n')

    assert "Section 'federation' must be a mapping" in message


def test_rejects_hostless_endpoint(tmp_path: Path) -> None:
    message = load_failure(tmp_path, BOT + 'federation:\n  endpoint: "not-a-url"\n')

    assert "federation.endpoint" in message


def test_rejects_malformed_endpoint(tmp_path: Path) -> None:
    message = load_failure(tmp_path, BOT + 'federation:\n  endpoint: "http://exa mple.com/"\n')

    assert "federation.endpoint" in message


def test_rejects_whitespace_in_access_token(tmp_path: Path) -> None:
    message = load_failure(
        tmp_path,
        BOT + 'federation:\n  endpoint: "http://127.0.0.1:7000/"\n  access_token: "abc def"\n',
    )

    assert "access_token" in message


def test_empty_access_token_is_absent(tmp_path: Path) -> None:
    configuration = load(
        tmp_path,
        BOT + 'federation:\n  endpoint: "http://127.0.0.1:7000/"\n  access_token: ""\n',
    )

    assert configuration.federation_access_token is None
