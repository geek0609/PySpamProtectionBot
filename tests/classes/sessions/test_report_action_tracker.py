from spb.classes.sessions.report_action_tracker import ReportActionTracker
from spb.objects.notification_target import NotificationTarget

CHAT = -1001234567890


def test_only_the_first_press_on_a_reported_message_wins() -> None:
    tracker = ReportActionTracker()
    tracker.track(CHAT, 50, [NotificationTarget(1, 10)])

    first = tracker.claim(CHAT, 50)
    assert first is not None
    assert len(first.targets) == 1
    assert tracker.claim(CHAT, 50) is None


def test_a_message_whose_state_is_gone_can_still_be_acted_on_once() -> None:
    tracker = ReportActionTracker()

    first = tracker.claim(CHAT, 51)
    assert first is not None
    assert first.targets == []
    assert tracker.claim(CHAT, 51) is None


def test_a_press_before_every_copy_is_delivered_is_not_undone_by_tracking() -> None:
    tracker = ReportActionTracker()

    assert tracker.claim(CHAT, 53) is not None
    assert not tracker.track(CHAT, 53, [NotificationTarget(1, 10)])
    assert tracker.claim(CHAT, 53) is None


def test_a_message_reported_twice_removes_the_buttons_of_both_reports() -> None:
    tracker = ReportActionTracker()
    tracker.track(CHAT, 52, [NotificationTarget(1, 10)])
    tracker.track(CHAT, 52, [NotificationTarget(2, 20)])

    claimed = tracker.claim(CHAT, 52)
    assert claimed is not None
    assert len(claimed.targets) == 2
