from spb.classes.update_handler import UpdateHandler, declaration_of, update_handler
from spb.enums import DispatchMode, UpdateType


def test_the_declaration_is_attached_to_the_decorated_class() -> None:
    @update_handler(UpdateType.COMMAND, commands=("ping",), priority=5)
    class Ping:
        pass

    assert declaration_of(Ping) == UpdateHandler(
        (UpdateType.COMMAND,), ("ping",), (), DispatchMode.ROUTE, 5, True
    )


def test_a_handler_declaring_no_types_serves_every_update() -> None:
    @update_handler(mode=DispatchMode.OBSERVE)
    class Everything:
        pass

    declaration = declaration_of(Everything)
    assert declaration is not None
    assert declaration.types == (UpdateType.ANY,)


def test_a_subclass_inherits_no_declaration() -> None:
    @update_handler(UpdateType.MESSAGE)
    class Base:
        pass

    class Derived(Base):
        pass

    assert declaration_of(Derived) is None
