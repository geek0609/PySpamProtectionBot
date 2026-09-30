"""The operator identity a Telegram user authenticated with."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OperatorIdentity:
    """Immutable snapshot of the operator identity a Telegram user has authenticated with.

    Instances are produced by OperatorManager and hold the credentials the Federation server
    accepted for the linked operator.

    Attributes:
        operator_uuid: The UUID of the operator on the Federation server.
        access_token: The access token the operator authenticated with.
    """

    operator_uuid: str
    access_token: str
