"""Turns what a user typed into an incident type.

Both /report and /blacklist take an incident type as a word from the user, and both accept the
same shorthands, so the mapping lives here rather than in whichever handler happened to need it
first.
"""

from pyfederation.enums import IncidentType

_ALIASES = {
    "illegal": IncidentType.ILLEGAL_CONTENT,
    "service": IncidentType.SERVICE_ABUSE,
    "abuse": IncidentType.SERVICE_ABUSE,
    "fraud": IncidentType.SCAM,
    "malicious": IncidentType.MALWARE,
    "phish": IncidentType.PHISHING,
}


def parse(token: str | None) -> IncidentType | None:
    """Parses a token as an incident type.

    Accepts case-insensitive type names, hyphenated type names, and the documented short aliases.

    Returns:
        The incident type, or None when the token does not match.
    """
    if token is None or not token.strip():
        return None

    normalized = token.lower().replace("-", "_")
    for incident_type in IncidentType:
        if incident_type.name.lower() == normalized:
            return incident_type

    return _ALIASES.get(normalized)
