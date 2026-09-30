from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, cast

from pyrogram.enums import ChatType
from pyrogram.types import Chat, Message

from tests.support import updates

_GENERATORS = {"get_chat_members"}
"""Methods that yield their answers one by one, like the Telegram methods that page."""


@dataclass(frozen=True)
class Call:
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class RecordingClient:
    """Stands in for the Telegram client, recording every call a handler makes.

    A method answers what the test set with respond(), a paging method yielding it item by item;
    otherwise methods that send or forward answer with a message numbered in calling order, so a
    caller can reply to what it sent, and every other method answers True.
    """

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._answers: dict[str, object] = {}

    def respond(self, method: str, answer: object) -> None:
        """Makes a method answer the given value from now on."""
        self._answers[method] = answer

    def __getattr__(self, method: str) -> Callable[..., Any]:
        if method in _GENERATORS:

            async def generate(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
                self.calls.append(Call(method, args, kwargs))
                for item in cast(list[Any], self._answers.get(method, [])):
                    yield item

            return generate

        async def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(Call(method, args, kwargs))
            if method in self._answers:
                return self._answers[method]
            if method.startswith(("send_", "forward_")):
                chat_id = kwargs.get("chat_id", args[0] if args else 0)
                chat = Chat(id=chat_id, type=ChatType.PRIVATE)
                sent = Message(id=len(self.calls), date=updates.DATE, chat=chat)
                # Like Telegram, several forwarded messages come back as a list.
                return [sent] if method == "forward_messages" else sent
            return True

        return call

    def named(self, method: str) -> list[Call]:
        """The calls made to one method, in order."""
        return [call for call in self.calls if call.method == method]
