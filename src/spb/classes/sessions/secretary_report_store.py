"""The first-contact messages that secretary notifications offer to report."""

import secrets
from datetime import timedelta

from spb.classes.cache import Cache
from spb.objects.secretary_report_target import SecretaryReportTarget

CACHE_MAX_SIZE = 10_000
EXPIRY = timedelta(days=7)
TOKEN_BYTES = 12


class SecretaryReportStore:
    """Remembers the first-contact messages that a secretary notification offers to report.

    Callback data is limited to 64 bytes, far too little for the message itself, so the Report
    button carries only an opaque token resolved here. The entries live in memory: once one expires
    or the bot restarts, the button stops working and the owner is told to forward the message
    instead.
    """

    def __init__(self) -> None:
        self._targets: Cache[str, SecretaryReportTarget] = Cache(CACHE_MAX_SIZE, EXPIRY)

    def store(self, target: SecretaryReportTarget) -> str:
        """Stores a reportable message and returns the opaque, URL-safe token that refers to it."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._targets.put(token, target)
        return token

    def find(self, token: str, owner_id: int) -> SecretaryReportTarget | None:
        """Returns the message the token refers to when it belongs to the given owner.

        Returns:
            The message, or None when it expired or belongs to somebody else.
        """
        target = self._targets.get_if_present(token)
        return target if target is not None and target.owner_id == owner_id else None
