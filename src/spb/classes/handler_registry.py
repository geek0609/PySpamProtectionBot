"""The set of handlers available to the bot, discovered from their declarations."""

import importlib
import inspect
import logging
import pkgutil
from collections.abc import Iterable
from typing import Self

from spb.classes.handler import Handler
from spb.classes.update_handler import declaration_of
from spb.enums import DispatchMode
from spb.exceptions import HandlerException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.registered_handler import RegisteredHandler

log = logging.getLogger(__name__)

DEFAULT_PACKAGE = "spb.handlers"
"""The package scanned when no other is given."""


class HandlerRegistry:
    """The set of handlers available to the bot, discovered from their declarations.

    Handlers are not registered by hand: the registry scans a package for classes decorated with
    update_handler(), instantiates each one, and sorts them into the two dispatch groups. A new
    feature therefore never touches start-up code: it is one decorated class under spb.handlers,
    and the bot picks it up on the next run.

    Within each group handlers are ordered by descending priority, then by class name so the order
    never depends on how the modules happened to be found.
    """

    def __init__(self, handlers: Iterable[Handler]) -> None:
        """Creates a registry from explicitly supplied handler instances.

        Discovery is how the bot builds its registry; this exists for tests and for the rare case
        where a handler needs constructor arguments and therefore cannot be discovered.

        Raises:
            HandlerException: If an instance is not decorated or its filters are contradictory.
        """
        registered = [RegisteredHandler.of(handler) for handler in handlers]
        registered.sort(
            key=lambda entry: (
                -entry.priority,
                f"{type(entry.handler).__module__}.{type(entry.handler).__qualname__}",
            )
        )
        self._observers = [entry for entry in registered if entry.mode is DispatchMode.OBSERVE]
        self._routed = [entry for entry in registered if entry.mode is DispatchMode.ROUTE]

    @classmethod
    def discover(cls, package: str = DEFAULT_PACKAGE) -> Self:
        """Discovers every decorated handler in the given package and its subpackages.

        Raises:
            HandlerException: If a handler cannot be instantiated.
        """
        handlers = []
        root = importlib.import_module(package)
        for module_info in pkgutil.walk_packages(root.__path__, f"{package}."):
            module = importlib.import_module(module_info.name)
            for candidate in vars(module).values():
                if not inspect.isclass(candidate) or candidate.__module__ != module.__name__:
                    continue

                declaration = declaration_of(candidate)
                if declaration is None:
                    continue

                if not declaration.enabled:
                    log.info(
                        "Handler %s is disabled and will not receive updates", candidate.__name__
                    )
                    continue

                handlers.append(_instantiate(candidate))

        registry = cls(handlers)
        log.info(
            "Registered %d handler(s) from '%s': %d observing, %d routed",
            len(registry),
            package,
            len(registry._observers),
            len(registry._routed),
        )
        return registry

    def observers_for(self, context: HandlerContext) -> list[Handler]:
        """Returns the observing handlers that match the update, in the order they must run."""
        return [entry.handler for entry in self._observers if _safe_matches(entry, context)]

    def route_for(self, context: HandlerContext) -> Handler | None:
        """Returns the first routed handler that claims the update, or None when none claims it."""
        return next(
            (entry.handler for entry in self._routed if _safe_matches(entry, context)), None
        )

    @property
    def handlers(self) -> list[RegisteredHandler]:
        """Every registered handler, observers first, each group in dispatch order."""
        return self._observers + self._routed

    def __len__(self) -> int:
        return len(self._observers) + len(self._routed)


def _safe_matches(registered: RegisteredHandler, context: HandlerContext) -> bool:
    """Evaluates a handler's filters, treating a failure as a non-match.

    A handler whose accepts() raises must not be able to stop the remaining handlers from seeing
    the update, so the failure is logged and the handler is skipped.
    """
    try:
        return registered.matches(context)
    except Exception:
        log.warning(
            "Handler %s failed while deciding whether to accept update %s",
            registered.name,
            context.update.update_id if context.update is not None else None,
            exc_info=True,
        )
        return False


def _instantiate(candidate: type) -> Handler:
    """Instantiates a discovered handler class.

    Raises:
        HandlerException: If the class does not extend Handler.
    """
    if not issubclass(candidate, Handler):
        raise HandlerException(
            f"{candidate.__module__}.{candidate.__qualname__} is decorated with @update_handler"
            " but does not extend Handler"
        )
    return candidate()
