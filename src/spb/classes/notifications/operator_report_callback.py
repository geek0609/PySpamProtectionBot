"""Callback data layout for the action buttons on #REPORT_ASSIGNED notifications.

The buttons carry the report UUID and the requested action directly, as
opr:<report_uuid>:<action>, so they stay usable for as long as the report is open and survive bot
restarts. The longest value (opr: + 36-character UUID + :SUSPICIOUS) is 51 bytes, inside
Telegram's 64-byte callback data limit.
"""

PREFIX = "opr"
"""Namespace of the notification buttons."""

CLOSE = "close"
"""Action that closes the report without a classification."""


def data(report_uuid: str, action: str) -> str:
    """Builds the callback data for one notification button.

    Args:
        report_uuid: The report the button acts on.
        action: CLOSE or a ClassificationFlag name.
    """
    return f"{PREFIX}:{report_uuid}:{action}"
