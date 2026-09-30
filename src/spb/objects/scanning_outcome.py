"""The outcome of one automated content scan."""

from dataclasses import dataclass

from pyfederation.enums import SuggestedAction

from spb.enums import ModerationAction


@dataclass(frozen=True, slots=True)
class ScanningOutcome:
    """Immutable snapshot of the outcome of one automated content scan.

    Carries every value describing how a scan resolved: the content and entity suggestions reported
    by the Federation server, and what was deleted, restricted or applied. Album restrictions are
    decided per album rather than per message, so they are not part of this; a scan of one part of
    an album reports what happened to that part.

    Attributes:
        content_suggestion: The suggested action for the scanned content, or None when the content
            was not scanned.
        entity_suggestion: The suggested action for the message author, or None when the author was
            not queried.
        deleted_message_ids: The ids of every message that was deleted.
        content_removal_requested: Whether content removal was requested, whatever came of it.
        entity_action: The moderation action resolved for the author.
        entity_action_applied: Whether that action was applied.
        text_restriction_applied: Whether the author was restricted to text.
    """

    content_suggestion: SuggestedAction | None
    entity_suggestion: SuggestedAction | None
    deleted_message_ids: tuple[int, ...]
    content_removal_requested: bool
    entity_action: ModerationAction
    entity_action_applied: bool
    text_restriction_applied: bool
