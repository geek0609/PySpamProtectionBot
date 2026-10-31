"""Selects and downloads the images of a Telegram message to scan with its content.

The files of scanned content (OFD 2.1) are only sent to a server that publishes limits for them,
within those limits, so a server that ignores files is sent none. Images are never named by URL
(OFD 2.2), since the download URL of a Telegram file carries the bot's token.
"""

import logging
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from pyfederation.records import ServerInformation
from pyrogram.types import Document, Message, Photo, Thumbnail

from spb.exceptions import FederationException
from spb.objects.context.handler_context import HandlerContext

log = logging.getLogger(__name__)


def has_image(message: Message) -> bool:
    """Whether the message carries a photo or an image document."""
    return message.photo is not None or _is_image(message.document)


def candidates(message: Message, information: ServerInformation) -> list[str]:
    """Selects the images of the message within the limits of the server: the largest photo size
    that fits `max_scan_file_size`, and an image document. A file whose size Telegram does not
    report is selected and checked once it is downloaded.

    Returns:
        The ids of the files to download, at most `max_scan_files`.
    """
    if information.max_scan_files <= 0 or information.max_scan_file_size <= 0:
        return []

    selected = []
    photo = message.photo
    if photo is not None:
        sizes: list[Photo | Thumbnail] = [photo, *(photo.thumbs or [])]
        fitting = [
            size
            for size in sizes
            if size.file_id
            and (not size.file_size or size.file_size <= information.max_scan_file_size)
        ]
        if fitting:
            largest = max(fitting, key=lambda size: (size.width or 0) * (size.height or 0))
            selected.append(largest.file_id)

    document = message.document
    if (
        _is_image(document)
        and document.file_id
        and (not document.file_size or document.file_size <= information.max_scan_file_size)
    ):
        selected.append(document.file_id)

    return selected[: information.max_scan_files]


@asynccontextmanager
async def collect(
    context: HandlerContext, message: Message, enabled: bool = True
) -> AsyncIterator[list[Path]]:
    """Downloads the images of the message to submit with its content, and deletes them once the
    block ends. A document keeps its file name. Failures are logged and skipped, the message is
    then scanned without the file.

    Args:
        context: The per-update context.
        message: The message whose images are scanned.
        enabled: Whether images may be sent at all, for example outside Privacy Mode.

    Yields:
        The files to submit with the content, empty when the server ignores files.
    """
    if not enabled or not context.federation.is_available or not has_image(message):
        yield []
        return

    try:
        information = await context.federation.scan_information()
    except FederationException as e:
        log.debug(
            "Unable to read the scanned file limits of the server, scanning without files: %s", e
        )
        yield []
        return

    with tempfile.TemporaryDirectory(prefix="spb-scan-") as directory:
        files = []
        for index, file_id in enumerate(candidates(message, information)):
            try:
                downloaded = Path(
                    await context.telegram_client.download_media(
                        file_id, file_name=f"{directory}/{index}/"
                    )
                )
                size = downloaded.stat().st_size
                if size == 0 or size > information.max_scan_file_size:
                    log.debug("Skipping Telegram file %s of %d bytes for scanning", file_id, size)
                    continue
                files.append(downloaded)
            except Exception as e:
                log.warning("Failed to download Telegram file %s for scanning: %s", file_id, e)
        yield files


def _is_image(document: Document | None) -> bool:
    return (
        document is not None
        and document.mime_type is not None
        and document.mime_type.lower().startswith("image/")
    )
