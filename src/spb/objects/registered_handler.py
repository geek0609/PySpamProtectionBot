"""A handler together with the dispatch rules it declared."""

from dataclasses import dataclass
from typing import Self

from spb.classes.handler import Handler
from spb.classes.update_handler import declaration_of
from spb.enums import DispatchMode, UpdateType
from spb.exceptions import HandlerException
from spb.objects.context.handler_context import HandlerContext


@dataclass(frozen=True, slots=True)
class RegisteredHandler:
    """A handler instance together with the dispatch rules read from its declaration.

    Reading the declaration once at start-up keeps the per-update path simple: routing an update
    is a walk over pre-resolved sets, not a lookup of what each handler declared.

    Attributes:
        handler: The shared handler instance.
        types: The update types the handler serves.
        commands: The command names the handler claims, lowercased; empty when it does not filter
            by command.
        callback_prefixes: The callback data prefixes the handler claims; empty when it does not
            filter by callback data.
        mode: How the handler participates in dispatch.
        priority: The evaluation order within the dispatch mode; higher runs first.
    """

    handler: Handler
    types: frozenset[UpdateType]
    commands: frozenset[str]
    callback_prefixes: tuple[str, ...]
    mode: DispatchMode
    priority: int

    @classmethod
    def of(cls, handler: Handler) -> Self:
        """Reads a handler's declaration into dispatch rules.

        Raises:
            HandlerException: If the class declares nothing, or its filters contradict the update
                types it declares.
        """
        name = f"{type(handler).__module__}.{type(handler).__qualname__}"
        declaration = declaration_of(type(handler))
        if declaration is None:
            raise HandlerException(f"{name} is not decorated with @update_handler")

        commands = set()
        for command in declaration.commands:
            normalized = command.strip().lower().removeprefix("/")
            if not normalized:
                raise HandlerException(f"{name} declares an empty command name")
            commands.add(normalized)

        if any(not prefix for prefix in declaration.callback_data):
            raise HandlerException(f"{name} declares an empty callback data prefix")

        types = frozenset(declaration.types)
        if commands and not types & {UpdateType.COMMAND, UpdateType.ANY}:
            raise HandlerException(
                f"{name} declares commands but not UpdateType.COMMAND, so it would never match"
            )

        if declaration.callback_data and not types & {UpdateType.CALLBACK_QUERY, UpdateType.ANY}:
            raise HandlerException(
                f"{name} declares callback data but not UpdateType.CALLBACK_QUERY, so it would"
                " never match"
            )

        return cls(
            handler,
            types,
            frozenset(commands),
            declaration.callback_data,
            declaration.mode,
            declaration.priority,
        )

    def matches(self, context: HandlerContext) -> bool:
        """Returns whether this handler should run for the update the context carries.

        The declared filters are evaluated first, cheapest first, and the handler's own accepts()
        hook is consulted last so it only sees updates that already passed the declaration.
        """
        update = context.update
        if update is None or not any(type.matches(update) for type in self.types):
            return False

        # Each filter constrains only the kind of update it can speak about: the command filter
        # judges command messages, the callback filter judges button presses, and neither has an
        # opinion about the other. That is what lets one handler serve /report, the plain replies
        # to its prompt, and its report:* buttons from a single declaration.
        if (
            self.commands
            and UpdateType.COMMAND.matches(update)
            and context.command_name not in self.commands
        ):
            return False

        if self.callback_prefixes and UpdateType.CALLBACK_QUERY.matches(update):
            data = context.callback_data
            if data is None or not data.startswith(self.callback_prefixes):
                return False

        return self.handler.accepts(context)

    @property
    def name(self) -> str:
        """The handler's name, used in log messages."""
        return self.handler.name
