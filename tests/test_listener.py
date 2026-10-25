import time
from queue import Queue
from unittest.mock import MagicMock

from uploadrr import scan
from uploadrr.listener import MonitorFolder


def test_on_closed_tar():
    queue = Queue()
    handler = MonitorFolder(queue)
    event = MagicMock(src_path="test.tar")
    handler.on_closed(event)
    assert queue.get() == "test.tar"
    assert "test.tar" in handler.processed_files


def test_on_closed_non_tar():
    queue = Queue()
    handler = MonitorFolder(queue)
    event = MagicMock(src_path="test.txt")
    handler.on_closed(event)
    assert queue.empty()


def test_on_closed_already_processed():
    queue = Queue()
    handler = MonitorFolder(queue)
    handler.processed_files.add("test.tar")
    event = MagicMock(src_path="test.tar")
    handler.on_closed(event)
    assert queue.empty()


def test_on_closed_reprocesses_after_processed_files_cleared():
    # Regression: on_closed used to also debounce rapid repeat events for the
    # same path via a last_event_time timestamp, but processed_files already
    # fully provides "process exactly once" for every real event sequence
    # (processed_files.add() happens unconditionally on every event that
    # passes the first check, so a real second on_closed for the same path
    # is always caught there first, with last_event_time's own entry already
    # deleted by the first call's cleanup) - the debounce branch could only
    # ever fire if something external re-seeded a recent timestamp while
    # processed_files was separately cleared, exactly like this test does.
    # That stray state must not delay reprocessing by one tick - it must be
    # reprocessed immediately. (Setting last_event_time here is harmless on
    # the fixed code, which no longer reads any such attribute.)
    queue = Queue()
    handler = MonitorFolder(queue)
    event = MagicMock(src_path="test.tar")

    handler.on_closed(event)
    assert queue.get_nowait() == "test.tar"

    handler.processed_files.clear()
    handler.last_event_time = {"test.tar": time.time()}

    handler.on_closed(event)
    assert queue.get_nowait() == "test.tar"


def test_on_closed_does_not_rescan_sibling_files(monkeypatch):
    # Characterization test locking in the documented retry cadence
    # (CLAUDE.md): a new tar arriving only enqueues itself - it must not
    # rescan the archive directory for older failed siblings. That's the
    # 24h periodic scan's job (or a restart), not on_closed's.
    backfill = MagicMock()
    add_files = MagicMock()
    monkeypatch.setattr(scan, "files_backfill", backfill)
    monkeypatch.setattr(scan, "add_files", add_files)

    queue = Queue()
    handler = MonitorFolder(queue)
    event = MagicMock(src_path="test.tar")

    handler.on_closed(event)

    assert queue.get_nowait() == "test.tar"
    backfill.assert_not_called()
    add_files.assert_not_called()
