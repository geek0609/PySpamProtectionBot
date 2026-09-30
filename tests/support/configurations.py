"""Writes the configuration files the tests start from."""

import os
from pathlib import Path
from tempfile import mkstemp

from spb.classes.configuration import Configuration

MINIMAL = """
bot:
  name: SpamProtectionBot
  api_key: "123456:ABCDEF"
  api_id: 12345
  api_hash: "0123456789abcdef0123456789abcdef"
"""


def minimal(directory: Path) -> Configuration:
    return write(directory, MINIMAL)


def privacy_mode(directory: Path) -> Configuration:
    return write(directory, MINIMAL + "  privacy_mode: true\n")


def write(directory: Path, content: str) -> Configuration:
    handle, name = mkstemp(prefix="configuration-", suffix=".yml", dir=directory)
    os.close(handle)
    Path(name).write_text(content, encoding="utf-8")
    return Configuration(name)
