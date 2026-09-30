"""A person who wrote to a secretary-enabled business connection."""

from dataclasses import dataclass

from spb.enums import SecretaryContactStatus


@dataclass(frozen=True, slots=True)
class SecretaryContact:
    """A person who wrote a message to a secretary-enabled business connection.

    Attributes:
        business_connection_id: The business connection the contact wrote to.
        id: The Telegram user id of the contact.
        status: The tracking status of the contact.
        first_seen_at: The Unix time the contact was first observed.
    """

    business_connection_id: str
    id: int
    status: SecretaryContactStatus
    first_seen_at: int
