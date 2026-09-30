"""Tests for declaration-driven handler discovery and routing."""

import pytest

from spb.classes.handler import Handler
from spb.classes.handler_registry import HandlerRegistry
from spb.classes.update_handler import update_handler
from spb.enums import DispatchMode, UpdateType
from spb.exceptions import HandlerException
from spb.objects.context.handler_context import HandlerContext
from spb.objects.update import Update
from tests.support import updates

TEST_PACKAGE = "tests.testhandlers"
"""The package holding the handler fixtures used by these tests."""


@pytest.fixture
def registry() -> HandlerRegistry:
    return HandlerRegistry.discover(TEST_PACKAGE)


def route_name(registry: HandlerRegistry, template: HandlerContext, update: Update) -> str:
    """Returns the class name of the handler that claims the update, or "none"."""
    handler = registry.route_for(template.with_update(update))
    return handler.name if handler is not None else "none"


def names(registry: HandlerRegistry) -> list[str]:
    return [registered.name for registered in registry.handlers]


def test_decorated_classes_in_the_package_are_registered(registry: HandlerRegistry) -> None:
    assert {"PingHandler", "DemoCallbackHandler", "ObservingHandler"} <= set(names(registry))


def test_a_handler_marked_disabled_is_not_registered(registry: HandlerRegistry) -> None:
    assert "DisabledHandler" not in names(registry)


def test_observers_and_routed_handlers_are_kept_apart(registry: HandlerRegistry) -> None:
    modes = [registered.mode for registered in registry.handlers]

    assert modes.count(DispatchMode.OBSERVE) == 3
    assert modes.count(DispatchMode.ROUTE) == 4
    assert len(registry) == 7


def test_observers_run_in_descending_priority_order(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    observers = registry.observers_for(template.with_update(updates.private_message(1, "hello")))

    assert [handler.name for handler in observers] == [
        "FailingObservingHandler",
        "ObservingHandler",
        "SecondObservingHandler",
    ]


def test_a_higher_priority_handler_claims_the_update_first(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.private_message(1, "/ping mine")

    assert route_name(registry, template, update) == "VetoingPingHandler"


def test_a_handler_that_declines_passes_the_update_to_the_next_one(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    assert (
        route_name(registry, template, updates.private_message(2, "/ping other")) == "PingHandler"
    )


def test_a_command_handler_claims_its_command(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    assert route_name(registry, template, updates.private_message(1, "/ping")) == "PingHandler"
    assert (
        route_name(registry, template, updates.group_message(2, "/ping@testbot")) == "PingHandler"
    )
    assert route_name(registry, template, updates.private_message(3, "/PING")) == "PingHandler"


def test_an_unclaimed_command_falls_through_to_the_catch_all_message_handler(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.private_message(4, "/unknown")

    assert route_name(registry, template, update) == "CatchAllMessageHandler"


def test_a_callback_handler_claims_only_its_data_prefix(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    demo = updates.callback_query(5, "demo:open")
    other = updates.callback_query(6, "other:open")

    assert route_name(registry, template, demo) == "DemoCallbackHandler"
    assert route_name(registry, template, other) == "none"


def test_an_update_no_handler_serves_is_left_unclaimed_but_observed(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.business_connection(7, True)

    assert route_name(registry, template, update) == "none"
    assert len(registry.observers_for(template.with_update(update))) == 3


def test_a_failing_accepts_is_a_non_match(template: HandlerContext) -> None:
    @update_handler(UpdateType.MESSAGE)
    class Refusing(Handler):
        def accepts(self, context: HandlerContext) -> bool:
            raise RuntimeError("cannot decide")

        async def handle(self, context: HandlerContext) -> None:
            pass

    registry = HandlerRegistry([Refusing()])

    assert registry.route_for(template.with_update(updates.private_message(1, "hi"))) is None


class Undecorated(Handler):
    """A handler that forgot its declaration."""

    async def handle(self, context: HandlerContext) -> None:
        pass


@update_handler(UpdateType.CALLBACK_QUERY, commands=("nope",))
class UnreachableCommands(Handler):
    """Declares commands it could never receive."""

    async def handle(self, context: HandlerContext) -> None:
        pass


@update_handler(UpdateType.MESSAGE, callback_data=("x:",))
class UnreachableCallbacks(Handler):
    """Declares callback data it could never receive."""

    async def handle(self, context: HandlerContext) -> None:
        pass


@update_handler(UpdateType.COMMAND, commands=(" ",))
class EmptyCommand(Handler):
    """Declares a command name that is not a name."""

    async def handle(self, context: HandlerContext) -> None:
        pass


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (Undecorated(), "@update_handler"),
        (UnreachableCommands(), "UpdateType.COMMAND"),
        (UnreachableCallbacks(), "UpdateType.CALLBACK_QUERY"),
        (EmptyCommand(), "empty command name"),
    ],
)
def test_invalid_declarations_are_rejected(handler: Handler, reason: str) -> None:
    with pytest.raises(HandlerException, match=reason):
        HandlerRegistry([handler])
