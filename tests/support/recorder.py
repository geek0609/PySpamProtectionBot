"""Records which handlers ran, in order.

Discovered handlers are instantiated by the registry, so a test cannot hold a reference to the
instance it wants to observe. They report here instead.
"""

from spb.objects.context.handler_context import HandlerContext

_invocations: list[str] = []


def record(handler: str, context: HandlerContext) -> None:
    """Records that a handler ran for an update."""
    update_id = context.update.update_id if context.update is not None else "none"
    _invocations.append(f"{handler}:{update_id}")


def invocations() -> list[str]:
    """The invocations recorded so far, in order."""
    return list(_invocations)


def handlers() -> list[str]:
    """The names of the handlers that ran, in order, without the update ids."""
    return [entry.partition(":")[0] for entry in _invocations]


def reset() -> None:
    """Discards everything recorded so far."""
    _invocations.clear()
