"""Tests which events let the entity publisher send an entity's properties."""

from spb.objects.context.handler_context import HandlerContext
from spb.utilities import entity_publisher

GROUP = -1004001234567


def test_a_private_chat_never_applies_privacy_mode(template: HandlerContext) -> None:
    assert not entity_publisher.privacy_applies(template, 42)


def test_a_group_applies_its_own_privacy_mode(template: HandlerContext) -> None:
    configurations = template.managers.chat_configurations
    configurations.enable_chat_configuration(GROUP)

    configurations.set_privacy_mode(GROUP, True)
    assert entity_publisher.privacy_applies(template, GROUP)

    configurations.set_privacy_mode(GROUP, False)
    assert not entity_publisher.privacy_applies(template, GROUP)
