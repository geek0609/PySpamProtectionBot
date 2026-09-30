import pytest
from pyfederation.enums import SuggestedAction
from pyfederation.records import EntityQueryResult

from spb.enums import JoinProtectionBehavior, ModerationAction, ScanningBehavior
from spb.utilities import entity_action_resolver

ENTITY = {
    "uuid": "0192aaaa-0000-7000-8000-000000000002",
    "hash": "hash",
    "host": "telegram.org",
    "id": "42",
    "whitelisted": False,
    "reputation": 0,
    "created": 1_700_000_000,
    "updated": 1_700_000_000,
}


def query(suggested_action: SuggestedAction | None) -> EntityQueryResult:
    return EntityQueryResult.model_validate(
        {
            "entity_record": ENTITY,
            "related_entities": [],
            "active_blacklists": [],
            "suggested_action": suggested_action,
        }
    )


@pytest.mark.parametrize("behaviors", [ScanningBehavior, JoinProtectionBehavior])
def test_strict_chats_ban_and_moderate_chats_restrict(
    behaviors: type[ScanningBehavior] | type[JoinProtectionBehavior],
) -> None:
    permanent = query(SuggestedAction.PERMANENTLY_BLOCK_ENTITY)
    temporary = query(SuggestedAction.TEMPORARILY_BLOCK_ENTITY)
    resolve = entity_action_resolver.resolve

    assert resolve(behaviors.STRICT, permanent) is ModerationAction.PERMANENT_BAN
    assert resolve(behaviors.STRICT, temporary) is ModerationAction.TEMPORARY_BAN
    assert resolve(behaviors.MODERATE, permanent) is ModerationAction.PERMANENT_RESTRICT
    assert resolve(behaviors.MODERATE, temporary) is ModerationAction.TEMPORARY_RESTRICT


@pytest.mark.parametrize("behaviors", [ScanningBehavior, JoinProtectionBehavior])
def test_nothing_is_done_without_a_blocking_suggestion_or_in_a_passive_chat(
    behaviors: type[ScanningBehavior] | type[JoinProtectionBehavior],
) -> None:
    resolve = entity_action_resolver.resolve

    assert resolve(behaviors.PASSIVE, query(SuggestedAction.PERMANENTLY_BLOCK_ENTITY)) is (
        ModerationAction.NONE
    )
    assert resolve(behaviors.STRICT, query(SuggestedAction.CAUTION)) is ModerationAction.NONE
    assert resolve(behaviors.STRICT, query(None)) is ModerationAction.NONE
    assert resolve(behaviors.STRICT, None) is ModerationAction.NONE
