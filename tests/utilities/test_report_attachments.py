from pyrogram.enums import ChatType
from pyrogram.types import Chat, Document, Message, Photo, User

from spb.objects.context.handler_context import HandlerContext
from spb.objects.report_attachment import ReportAttachment
from spb.utilities import report_attachments
from tests.support import updates

GROUP = Chat(id=-100, type=ChatType.SUPERGROUP, title="Group")


def photo(file_id: str) -> Photo:
    return Photo(
        file_id=file_id,
        file_unique_id=f"unique-{file_id}",
        width=1280,
        height=720,
        file_size=1024,
        date=updates.DATE,
    )


def album_part(message_id: int, author: User, file_id: str) -> Message:
    return Message(
        id=message_id,
        date=updates.DATE,
        chat=GROUP,
        from_user=author,
        media_group_id="album-1",
        photo=photo(file_id),
    )


def test_a_message_carries_its_primary_files_once() -> None:
    message = Message(
        id=1,
        date=updates.DATE,
        chat=GROUP,
        document=Document(file_id="doc", file_unique_id="unique-doc", file_name="spam.pdf"),
        photo=photo("doc"),
    )

    assert report_attachments.collect(message) == [ReportAttachment("doc", "spam.pdf")]


def test_a_report_carries_every_part_of_an_album_seen_so_far(template: HandlerContext) -> None:
    report_attachments.for_report(template, album_part(1, updates.TESTER, "first"))

    attachments = report_attachments.for_report(template, album_part(2, updates.TESTER, "second"))

    assert attachments == [ReportAttachment("first", None), ReportAttachment("second", None)]


def test_an_album_is_kept_apart_from_another_authors_parts(template: HandlerContext) -> None:
    stranger = User(id=99, is_bot=False, first_name="Stranger")
    report_attachments.for_report(template, album_part(1, updates.TESTER, "first"))

    attachments = report_attachments.for_report(template, album_part(2, stranger, "second"))

    assert attachments == [ReportAttachment("second", None)]
