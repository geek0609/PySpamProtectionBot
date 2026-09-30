"""Collects every primary downloadable file carried by a Telegram message."""

from pyrogram.types import Message

from spb.classes import update_dispatcher
from spb.objects.context.handler_context import HandlerContext
from spb.objects.report_attachment import ReportAttachment


def collect(message: Message) -> list[ReportAttachment]:
    """Returns the message's unique, downloadable file attachments in stable presentation order.

    Thumbnails are deliberately excluded; Telegram exposes them as derivatives of a primary file,
    and a photo is its largest size.
    """
    attachments: dict[str, ReportAttachment] = {}

    def add(file_id: str, file_name: str | None) -> None:
        attachments.setdefault(file_id, ReportAttachment(file_id, file_name))

    if message.document is not None:
        add(message.document.file_id, message.document.file_name)
    if message.audio is not None:
        add(message.audio.file_id, message.audio.file_name)
    if message.video is not None:
        add(message.video.file_id, message.video.file_name)
    if message.animation is not None:
        add(message.animation.file_id, message.animation.file_name)
    if message.voice is not None:
        add(message.voice.file_id, None)
    if message.video_note is not None:
        add(message.video_note.file_id, None)
    if message.sticker is not None:
        add(message.sticker.file_id, None)
    if message.photo is not None:
        add(message.photo.file_id, None)
    return list(attachments.values())


def for_report(context: HandlerContext, message: Message) -> list[ReportAttachment]:
    """Returns the attachments a report of the message carries: those of its whole album when it is
    part of one whose attachments were seen, otherwise its own."""
    media_group = update_dispatcher.media_group(context, message)
    if media_group is None or not media_group.attachments:
        return collect(message)
    return list(media_group.attachments.values())
