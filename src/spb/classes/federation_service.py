"""Everything the bot asks of a Federation server."""

import asyncio
from collections.abc import Awaitable, Callable
from os import PathLike
from pathlib import Path
from typing import Any

from pyfederation import AsyncFederationClient
from pyfederation.enums import ClassificationFlag, EntityRelationshipType, IncidentType
from pyfederation.exceptions import FederationClientException
from pyfederation.records import (
    BlacklistRecord,
    ContentInput,
    EntityQueryResult,
    EntityRecord,
    EvidenceRecord,
    FileAttachmentRecord,
    OperatorRecord,
    ReportRecord,
    ReportSubmission,
    ScannedContent,
    ServerInformation,
)

from spb.exceptions import FederationException

BLACKLIST_PAGE_SIZE = 5
"""How many blacklist records are read for an entity before the rest are ignored."""


class FederationService:
    """Everything the bot asks of a Federation server.

    Handlers talk to this rather than to the client library, for three reasons. It names the
    operations the bot performs out of the client's many, so the dependency is legible. It
    turns the client's failures into one FederationException, so a handler cannot forget that the
    network is involved. And a single unavailable() instance stands in for handler tests, so they
    never need a live server.

    Federation is optional: when the configuration has no federation section the bot runs with
    unavailable(), which reports itself unavailable, and the features that need it say so rather
    than failing. Every caller must check is_available first, or be prepared for
    FederationException.

    A server accepts an anonymous client for whatever it has marked public, but every write and some
    reads require an authenticated one. Server-side, that bar is not "a token was sent" but client
    permissions: a token that identifies a disabled operator or one with no client permissions is,
    for every practical purpose, worse than no token at all, since the server holds it to the
    authenticated standard without it clearing that standard. authenticate() is how the bot finds
    out which side of that line its own token falls on, and is_authenticated is what callers that
    only make sense as the bot itself (publishing an entity, submitting a report as the bot rather
    than an operator) check first, so those features are skipped rather than attempted and rejected.
    This is unrelated to an operator's own /auth session, which carries its own access token through
    each call and is unaffected by the bot's own authentication state.

    Only a definitive answer about the token settles it: a token the server rejects, or one that
    identifies an operator without client permissions, is discarded and the service carries on as an
    anonymous client. A server that is unreachable or failing says nothing about the token, so it is
    kept and authentication stays pending (see is_authentication_pending) until a later
    authenticate() gets an answer.
    """

    def __init__(self, endpoint: str | None, access_token: str | None = None) -> None:
        """
        Args:
            endpoint: The base URL of the server, or None for the unavailable instance.
            access_token: The bot's own access token, or None for anonymous access.
        """
        self._endpoint = endpoint
        self._access_token = access_token
        self._client = AsyncFederationClient(endpoint, access_token) if endpoint else None
        self._authenticated = False
        self._authentication = asyncio.Lock()

    @classmethod
    def unavailable(cls) -> "FederationService":
        """Returns the shared instance used when the configuration has no federation section.

        The bot is designed to run without Federation: scanning, reporting and blacklisting
        announce themselves as unavailable while moderation, configuration and localization keep
        working. Having an instance that says so is what lets every handler check is_available
        instead of testing for None.
        """
        return _UNAVAILABLE

    @property
    def is_available(self) -> bool:
        """Whether a Federation server is configured and can be called at all.

        This reports configuration, not reachability: a configured server that is currently down
        is still available and its calls fail with FederationException.
        """
        return self._client is not None

    @property
    def is_authenticated(self) -> bool:
        """Whether the bot's own access token carries client permissions, as of authenticate().

        This is False until authenticate() has run, for an anonymous client, and for a token the
        server accepted but did not grant client permissions to. Callers that act as the bot itself
        for a write the server never allows anonymously check this first, so the attempt is skipped
        rather than made and rejected.
        """
        return self._authenticated

    @property
    def is_authentication_pending(self) -> bool:
        """Whether the bot has an access token whose standing the server has not yet settled, so
        authenticate() is worth calling again.

        This is the case before the first authenticate() and after any attempt that failed without
        the server answering for the token, typically because it was unreachable. It is False once
        the token is known to grant client permissions, once it has been discarded, and when no
        token was configured.
        """
        token = self._access_token
        return (
            self._client is not None
            and token is not None
            and bool(token.strip())
            and not self._authenticated
        )

    @property
    def endpoint(self) -> str | None:
        """The base URL of the configured server, or None when Federation is not configured."""
        return self._endpoint

    async def authenticate(self) -> OperatorRecord | None:
        """Identifies the operator the bot's own access token belongs to, and records whether it
        grants client permissions.

        A token the server rejects or that lacks client permissions is discarded, and every later
        call is made anonymously. Any other failure leaves the token in place and authentication
        pending, so a server that was down when this was called can be authenticated against once
        it is back.

        Returns:
            The identified operator, or None when Federation is not configured or there is no
            access token, since an anonymous client has nothing to identify.

        Raises:
            FederationException: If the server could not be reached or rejected the token.
        """
        async with self._authentication:
            if not self.is_authentication_pending and not self._authenticated:
                return None

            client = self._require_available()
            try:
                operator = await self._call("read the bot's own operator record", client.get_self())
            except FederationException as e:
                if self.is_credential_rejected(e):
                    await self._discard_access_token()
                raise

            self._authenticated = operator.client_permissions or operator.management_permissions
            if not self._authenticated:
                await self._discard_access_token()
            return operator

    async def _discard_access_token(self) -> None:
        """Replaces the client with an anonymous one, since a token without client permissions is
        held to a stricter standard than no token at all."""
        previous = self._client
        self._client = AsyncFederationClient(str(self._endpoint))
        self._access_token = None
        self._authenticated = False
        if previous is not None:
            await previous.aclose()

    async def server_information(self) -> ServerInformation:
        """Returns the server's name, version and record counts."""
        client = self._require_available()
        return await self._call("read server information", client.get_server_information())

    async def operator_for(self, access_token: str) -> OperatorRecord:
        """Returns the operator an access token belongs to, without changing the bot's own
        credentials."""
        self._require_available()
        return await self._as_operator(
            access_token, "identify an operator", lambda operator: operator.get_self()
        )

    async def entity(self, entity: str | None) -> EntityRecord | None:
        """Returns the Federation record for an entity address, UUID or hash, or None when nothing
        matches."""
        client = self._require_available()
        if entity is None or not entity.strip():
            return None
        return await self._optional(f"look up entity {entity}", client.get_entity_record(entity))

    async def query_entity(self, identifier: str | None) -> EntityQueryResult | None:
        """Asks the server what it recommends doing about an entity.

        Unlike entity(), which returns what is stored, this returns the server's assessment: the
        suggested action, when it lifts, and the records behind it. It is what join protection and
        scanning act on.

        Returns:
            The assessment, or None when the server knows nothing.
        """
        client = self._require_available()
        if identifier is None or not identifier.strip():
            return None
        return await self._optional(f"query entity {identifier}", client.query_entity(identifier))

    async def publish_entity(
        self, host: str, identifier: str | None, metadata: dict[str, Any] | None
    ) -> str:
        """Registers an entity with the server.

        Args:
            host: The host the identifier belongs to, such as telegram.org, or for a host entity
                the canonical domain name or IP address itself.
            identifier: The identifier within that host, such as a Telegram user id, or None for a
                host entity.
            metadata: Additional attributes to publish, or None for none.

        Returns:
            The UUID the server assigned the entity.
        """
        client = self._require_available()
        self._require_client_permissions("publish an entity")
        address = f"{identifier}@{host}" if identifier is not None else host
        return await self._call(
            f"publish entity {address}", client.push_entity(host, identifier, metadata)
        )

    async def link_entities(
        self,
        access_token: str | None,
        entity: str,
        target: str,
        relationship: EntityRelationshipType,
    ) -> None:
        """Records a relationship between two entities, as the operator whose access token is
        given or, without one, as the bot."""
        client = self._require_available()
        what = f"link {entity} to {target}"
        if access_token is None:
            await self._call(what, client.set_entity_relationship(entity, target, relationship))
            return

        await self._as_operator(
            access_token,
            what,
            lambda operator: operator.set_entity_relationship(entity, target, relationship),
        )

    async def scan_content(self, content: ContentInput, entity: str | None) -> ScannedContent:
        """Scans content for spam, phishing, malware and other threats.

        Args:
            content: The content to scan, with whatever metadata the caller chose to attach.
            entity: The entity the content came from, or None to scan anonymously.
        """
        client = self._require_available()
        return await self._call("scan content", client.scan_content(content, entity))

    async def active_blacklists(self, entity: str) -> list[BlacklistRecord]:
        """Returns the blacklist records currently active against an entity."""
        client = self._require_available()
        return await self._call(
            f"list blacklists for {entity}",
            client.list_entity_blacklist_records(entity, 1, BLACKLIST_PAGE_SIZE, False),
        )

    async def blacklist_entity(
        self,
        access_token: str | None,
        entity: str,
        report: str,
        incident_type: IncidentType,
        expires: int | None,
    ) -> str:
        """Blacklists an entity against an existing report.

        Args:
            access_token: The operator's access token, or None to act as the bot.
            entity: The entity to blacklist.
            report: The UUID of the supporting report.
            incident_type: The incident type.
            expires: The Unix time the blacklist expires, or None for permanent.

        Returns:
            The UUID of the created blacklist record.
        """
        client = self._require_available()
        what = f"blacklist {entity}"
        if access_token is None:
            return await self._call(
                what, client.blacklist_entity(entity, incident_type, report, expires)
            )
        return await self._as_operator(
            access_token,
            what,
            lambda operator: operator.blacklist_entity(entity, incident_type, report, expires),
        )

    async def report(self, uuid: str | None) -> ReportRecord | None:
        """Returns a report by its UUID, or None when nothing matches."""
        client = self._require_available()
        if uuid is None or not uuid.strip():
            return None
        return await self._optional(f"look up report {uuid}", client.get_report(uuid))

    async def report_as(self, access_token: str, uuid: str | None) -> ReportRecord | None:
        """Returns a report by its UUID as seen by an operator, who may see reports the bot's own
        client cannot.

        Returns:
            The report, or None when nothing matches.
        """
        self._require_available()
        if uuid is None or not uuid.strip():
            return None

        try:
            return await self._as_operator(
                access_token, f"look up report {uuid}", lambda operator: operator.get_report(uuid)
            )
        except FederationException as e:
            if isinstance(e.__cause__, FederationClientException) and _is_not_found(e.__cause__):
                return None
            raise

    async def entity_reports(self, entity: str, limit: int) -> list[ReportRecord]:
        """Returns the reports filed against an entity."""
        client = self._require_available()
        return await self._call(
            f"list reports for {entity}", client.list_entity_reports(entity, 1, limit)
        )

    async def open_assigned_reports(self, access_token: str, limit: int) -> list[ReportRecord]:
        """Returns the reports that are open and assigned to the given operator, oldest first."""
        self._require_available()
        return await self._as_operator(
            access_token,
            "list open reports",
            lambda operator: operator.list_opened_reports(1, limit, "created", "ASC"),
        )

    async def submit_report(
        self,
        access_token: str | None,
        entity: str,
        evidence: ContentInput,
        incident_type: IncidentType,
        message: str | None,
    ) -> ReportSubmission:
        """Submits a report.

        Args:
            access_token: The operator's access token, or None to submit as the bot.
            entity: The entity being reported.
            evidence: The reported content and its tag.
            incident_type: The incident type.
            message: The reporter's comment, or None.

        Returns:
            The server's acknowledgement, carrying the new report UUID.
        """
        client = self._require_available()
        what = f"submit a report against {entity}"
        if access_token is None:
            self._require_client_permissions(what)
            return await self._call(
                what, client.submit_report(entity, evidence, incident_type, message)
            )
        return await self._as_operator(
            access_token,
            what,
            lambda operator: operator.submit_report(entity, evidence, incident_type, message),
        )

    async def close_report(
        self, access_token: str, uuid: str, classification: ClassificationFlag | None
    ) -> None:
        """Closes a report as the operator whose access token is given, optionally classifying
        it."""
        self._require_available()
        await self._as_operator(
            access_token,
            f"close report {uuid}",
            lambda operator: operator.close_report(uuid, classification),
        )

    async def evidence_as(
        self, access_token: str | None, uuid: str | None
    ) -> EvidenceRecord | None:
        """Returns an evidence record, read as a particular operator.

        Confidential evidence is only visible to the operator it belongs to, so a lookup made on
        behalf of an authenticated operator must carry their token rather than the bot's.

        Args:
            access_token: The operator's access token, or None to read as the bot.
            uuid: The evidence UUID.

        Returns:
            The record, or None when nothing matches.
        """
        client = self._require_available()
        if uuid is None or not uuid.strip():
            return None

        what = f"look up evidence {uuid}"
        if access_token is None:
            return await self._optional(what, client.get_evidence_record(uuid))

        try:
            return await self._as_operator(
                access_token, what, lambda operator: operator.get_evidence_record(uuid)
            )
        except FederationException as e:
            if isinstance(e.__cause__, FederationClientException) and _is_not_found(e.__cause__):
                return None
            raise

    async def evidence_attachments(
        self, access_token: str | None, evidence_uuid: str
    ) -> list[FileAttachmentRecord]:
        """Returns the files attached to an evidence record, read as the operator whose access
        token is given or, without one, as the bot."""
        client = self._require_available()
        what = f"list attachments for evidence {evidence_uuid}"
        if access_token is None:
            return await self._call(what, client.get_evidence_attachments(evidence_uuid))
        return await self._as_operator(
            access_token, what, lambda operator: operator.get_evidence_attachments(evidence_uuid)
        )

    async def download_attachment(
        self,
        access_token: str | None,
        attachment_uuid: str,
        destination_directory: str | PathLike[str],
    ) -> Path:
        """Downloads an attachment into a local directory, returning the path of the file."""
        client = self._require_available()
        what = f"download attachment {attachment_uuid}"
        if access_token is None:
            return await self._call(
                what, client.download_attachment(attachment_uuid, destination_directory)
            )
        return await self._as_operator(
            access_token,
            what,
            lambda operator: operator.download_attachment(attachment_uuid, destination_directory),
        )

    async def report_evidence(self, uuid: str, limit: int) -> list[EvidenceRecord]:
        """Returns the evidence attached to a report."""
        client = self._require_available()
        return await self._call(
            f"list evidence for report {uuid}", client.list_report_evidence_records(uuid, 1, limit)
        )

    async def upload_attachment(
        self,
        access_token: str | None,
        evidence_uuid: str,
        file_path: str | PathLike[str],
        file_name: str | None,
    ) -> str:
        """Uploads a file as an attachment to an evidence record.

        Args:
            access_token: The operator's access token, or None to upload as the bot.
            evidence_uuid: The evidence the file belongs to.
            file_path: The local path of the file to upload.
            file_name: The name to store the file under.

        Returns:
            The UUID of the stored attachment.
        """
        client = self._require_available()
        what = f"upload an attachment to evidence {evidence_uuid}"
        if access_token is None:
            self._require_client_permissions(what)
            result = await self._call(
                what, client.upload_file_attachment(evidence_uuid, file_path, file_name)
            )
        else:
            result = await self._as_operator(
                access_token,
                what,
                lambda operator: operator.upload_file_attachment(
                    evidence_uuid, file_path, file_name
                ),
            )
        return result.uuid

    async def close(self) -> None:
        """Releases whatever the instance holds. Safe to call more than once, and harmless on the
        unavailable instance."""
        if self._client is not None:
            await self._client.aclose()

    @staticmethod
    def is_credential_rejected(e: FederationException) -> bool:
        """Returns whether a failure means the server rejected the access token itself, as opposed
        to being unreachable or failing for some other reason.

        The server answers 401 for a token it does not recognise and 403 for one belonging to a
        disabled operator. Either way, retrying with the same token cannot succeed, so a caller
        holding a stored operator credential should discard it. A failure with no HTTP response
        (status 0) or any other status says nothing about the token.
        """
        cause = e.__cause__
        return isinstance(cause, FederationClientException) and cause.status_code in (401, 403)

    def _require_available(self) -> AsyncFederationClient:
        """Returns the client, failing with the "not configured" message on the unavailable
        instance."""
        if self._client is None:
            raise FederationException("Federation is not configured")
        return self._client

    def _require_client_permissions(self, what: str) -> None:
        """Fails fast, without a network round trip, when the bot's own token does not carry client
        permissions.

        The server rejects these calls unconditionally when acting as the bot, since there is no
        anonymous path for them, so there is nothing to gain from attempting one first.
        """
        if not self._authenticated:
            raise FederationException(
                f"Failed to {what}: This action requires the bot's own Federation access token to "
                "carry client permissions"
            )

    @staticmethod
    async def _call[T](what: str, call: Awaitable[T]) -> T:
        """Awaits a call against the shared client, translating its failures."""
        try:
            return await call
        except (FederationClientException, ValueError) as e:
            raise FederationException(f"Failed to {what}: {e}") from e

    @staticmethod
    async def _optional[T](what: str, call: Awaitable[T]) -> T | None:
        """Awaits a lookup that may legitimately find nothing."""
        try:
            return await call
        except FederationClientException as e:
            if _is_not_found(e):
                return None
            raise FederationException(f"Failed to {what}: {e}") from e
        except ValueError as e:
            raise FederationException(f"Failed to {what}: {e}") from e

    async def _as_operator[T](
        self,
        access_token: str,
        what: str,
        call: Callable[[AsyncFederationClient], Awaitable[T]],
    ) -> T:
        """Runs a call as a particular operator, on a client bound to that operator's token."""
        try:
            async with AsyncFederationClient(str(self._endpoint), access_token) as operator:
                return await call(operator)
        except (FederationClientException, ValueError) as e:
            raise FederationException(f"Failed to {what}: {e}") from e


def _is_not_found(e: FederationClientException) -> bool:
    """Returns whether a failure means "the server has no such record" rather than a real error."""
    message = str(e).lower()
    return "404" in message or "not found" in message or "does not exist" in message


_UNAVAILABLE = FederationService(None)
