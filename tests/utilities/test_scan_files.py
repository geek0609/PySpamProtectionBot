from pyfederation.records import ServerInformation
from pyrogram.enums import ChatType
from pyrogram.types import Chat, Document, Message, Photo, Thumbnail

from spb.utilities import scan_files
from tests.support import payloads, updates

GROUP = Chat(id=-100, type=ChatType.SUPERGROUP, title="Group")


def limits(max_files: int, max_file_size: int) -> ServerInformation:
    return ServerInformation.model_validate(
        payloads.server_information(max_scan_files=max_files, max_scan_file_size=max_file_size)
    )


def thumbnail(file_id: str, side: int, file_size: int) -> Thumbnail:
    return Thumbnail(
        file_id=file_id,
        file_unique_id=f"unique-{file_id}",
        width=side,
        height=side,
        file_size=file_size,
    )


def photo_message() -> Message:
    photo = Photo(
        file_id="large",
        file_unique_id="unique-large",
        width=2560,
        height=2560,
        file_size=5_000_000,
        date=updates.DATE,
        thumbs=[thumbnail("small", 90, 2_000), thumbnail("medium", 800, 80_000)],
    )
    return Message(id=1, date=updates.DATE, chat=GROUP, photo=photo)


def document_message(file_id: str, file_name: str, mime_type: str) -> Message:
    document = Document(
        file_id=file_id,
        file_unique_id=f"unique-{file_id}",
        file_name=file_name,
        mime_type=mime_type,
        file_size=1_000,
    )
    return Message(id=1, date=updates.DATE, chat=GROUP, document=document)


def test_selects_the_largest_photo_size_within_the_size_limit() -> None:
    message = photo_message()

    assert scan_files.candidates(message, limits(4, 100_000)) == ["medium"]
    assert scan_files.candidates(message, limits(4, 10_485_760)) == ["large"]


def test_selects_image_documents_only() -> None:
    image = document_message("qr", "qr.png", "image/png")
    archive = document_message("zip", "spam.zip", "application/zip")

    assert scan_files.has_image(image)
    assert scan_files.candidates(image, limits(4, 10_485_760)) == ["qr"]
    assert not scan_files.has_image(archive)
    assert scan_files.candidates(archive, limits(4, 10_485_760)) == []


def test_selects_nothing_from_a_server_that_ignores_files() -> None:
    message = photo_message()

    assert scan_files.candidates(message, limits(0, 0)) == []
    information = ServerInformation.model_validate(payloads.server_information())
    assert scan_files.candidates(message, information) == []


def test_keeps_within_the_file_limit() -> None:
    message = photo_message()
    message.document = document_message("qr", "qr.png", "image/png").document

    assert scan_files.candidates(message, limits(1, 10_485_760)) == ["large"]
    assert scan_files.candidates(message, limits(4, 10_485_760)) == ["large", "qr"]
