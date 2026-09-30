from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from pyrogram import Client

from spb.objects.context.handler_context import HandlerContext
from tests.support import contexts
from tests.support.recording_client import RecordingClient


@pytest.fixture
def client() -> RecordingClient:
    return RecordingClient()


@pytest.fixture
def context(tmp_path: Path, client: RecordingClient) -> Iterator[HandlerContext]:
    """A template context whose Telegram client records what the handler sends."""
    context = contexts.template(tmp_path, client=cast(Client, client))
    yield context
    context.database.close()
