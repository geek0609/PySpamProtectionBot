from collections.abc import Iterator
from pathlib import Path

import pytest

from spb.objects.context.handler_context import HandlerContext
from tests.support import contexts


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def template(tmp_path: Path) -> Iterator[HandlerContext]:
    """A template context over a throwaway database."""
    context = contexts.template(tmp_path)
    yield context
    context.database.close()
