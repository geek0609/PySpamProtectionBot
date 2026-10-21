"""Federation responses for the tests that talk to a mocked server."""

from typing import Any


def server_information(**changes: Any) -> dict[str, Any]:
    """The response to GET /info, with the given members changed."""
    return {
        "name": "Federation Server",
        "api_version": "2.0",
        "public_audit_logs": True,
        "public_evidence": True,
        "public_blacklist": True,
        "public_entities": True,
        "public_reports": True,
        "public_entity_metadata": False,
        "public_telephone_entities": False,
        "public_scan_content": True,
        "public_query_entity": True,
        "allow_illegal_content": True,
        "search_enabled": False,
        "public_search": False,
        "search_types": [],
        "public_search_types": [],
        **changes,
    }
