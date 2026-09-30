"""Declares a class as a handler and describes which updates it serves."""

from collections.abc import Callable
from dataclasses import dataclass

from spb.enums import DispatchMode, UpdateType

_ATTRIBUTE = "__update_handler__"


@dataclass(frozen=True, slots=True)
class UpdateHandler:
    """Which updates a handler serves, as declared by update_handler().

    An update must match one of the types. Each of commands and callback_data then constrains only
    the kind of update it can speak about: the command filter judges command messages and the
    callback filter judges button presses, and neither has an opinion about the other. A handler
    that serves /help and its help:* buttons declares both and each update is judged by the one
    that applies; a handler that wants every message of a type plus a callback namespace declares
    only the callback filter. A handler may narrow the match further at runtime by overriding
    Handler.accepts().

    Attributes:
        types: The kinds of update the handler serves.
        commands: The command names the handler claims, without the leading slash. Matching is
            case-insensitive and accepts the /command@botname form. Empty when the handler does not
            filter by command name.
        callback_data: The callback data prefixes the handler claims. A button press matches when
            its callback data starts with any of them, which is how a handler owns a namespace such
            as "settings:". Empty when the handler does not filter by callback data.
        mode: How the handler participates in dispatch once its types match.
        priority: The evaluation order within the handler's dispatch mode; higher runs first.
            Handlers of equal priority are ordered by class name, so the dispatch order never
            depends on the order in which the modules happened to be found.
        enabled: Whether the handler is registered at all, which keeps a half-finished or
            temporarily disabled handler in the source tree without it receiving updates.
    """

    types: tuple[UpdateType, ...]
    commands: tuple[str, ...]
    callback_data: tuple[str, ...]
    mode: DispatchMode
    priority: int
    enabled: bool


def update_handler[T: type](
    *types: UpdateType,
    commands: tuple[str, ...] = (),
    callback_data: tuple[str, ...] = (),
    mode: DispatchMode = DispatchMode.ROUTE,
    priority: int = 0,
    enabled: bool = True,
) -> Callable[[T], T]:
    """Declares a class as a handler and describes which updates it serves.

    Handlers are discovered by HandlerRegistry from this declaration instead of being registered by
    hand at start-up, so adding a feature means adding one decorated class under spb.handlers and
    nothing else. The decorated class must extend Handler, must not be abstract, and must take no
    constructor arguments; exactly one instance of it is created and shared by every worker task,
    so it must not keep mutable per-update state in attributes.

    Example: a handler for /start and /help that is consulted before the general command handlers.

        @update_handler(UpdateType.COMMAND, commands=("start", "help"), priority=100)
        class StartHandler(Handler): ...

    Args:
        types: The kinds of update the handler serves, every update when none are given.
    """
    declaration = UpdateHandler(
        types or (UpdateType.ANY,), commands, callback_data, mode, priority, enabled
    )

    def declare(cls: T) -> T:
        setattr(cls, _ATTRIBUTE, declaration)
        return cls

    return declare


def declaration_of(cls: type) -> UpdateHandler | None:
    """Returns what a class declared through update_handler(), or None when it declared nothing.

    A declaration belongs to the class it decorates; a subclass inherits none.
    """
    declaration: UpdateHandler | None = cls.__dict__.get(_ATTRIBUTE)
    return declaration
