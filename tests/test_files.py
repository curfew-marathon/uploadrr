import os
from queue import Queue
from unittest.mock import MagicMock, patch

from uploadrr import files, metrics
from uploadrr.files import add_files


def test_add_files(tmp_path):
    queue = Queue()
    (tmp_path / "test1.tar").write_text("data")
    (tmp_path / "test2.txt").write_text("data")
    os.mkdir(tmp_path / "subdir")

    add_files(str(tmp_path), queue)
    assert queue.get() == os.path.join(str(tmp_path), "test1.tar")
    assert queue.empty()


def test_add_files_not_found():
    queue = Queue()
    with patch("uploadrr.files.logger") as mock_logger:
        add_files("/non-existent-path", queue)
        mock_logger.warning.assert_called_with(
            "Archive directory not found: %s", "/non-existent-path"
        )
        assert queue.empty()


def test_count_pending_tars_counts_only_tar_files(tmp_path):
    (tmp_path / "a.tar").write_text("data")
    (tmp_path / "b.tar").write_text("data")
    (tmp_path / "c.txt").write_text("data")
    os.mkdir(tmp_path / "subdir.tar")  # a directory named *.tar must not count

    assert files.count_pending_tars(str(tmp_path)) == 2


def test_count_pending_tars_missing_dir_returns_zero():
    assert files.count_pending_tars("/non-existent-path") == 0


def test_add_files_sets_pending_tars_gauge(monkeypatch, tmp_path):
    (tmp_path / "a.tar").write_text("data")
    (tmp_path / "b.tar").write_text("data")
    fake_config = MagicMock()
    fake_config.get_serial.return_value = "test_serial"
    monkeypatch.setattr(files, "CONFIG", fake_config)

    add_files(str(tmp_path), Queue())

    assert metrics.PENDING_TARS.labels(serial="test_serial")._value.get() == 2


def _labeled(counter, **labels):
    return counter.labels(**labels)._value.get()


def _run_launch_once(monkeypatch, process_side_effect):
    """Drive files.launch() through exactly one queued file, then exit the
    loop the same way it always exits: a KeyboardInterrupt. Observer/backfill
    are stubbed so this doesn't touch the filesystem or start real threads."""
    monkeypatch.setattr(files, "Observer", lambda: MagicMock())
    monkeypatch.setattr(files, "files_backfill", lambda q: q.put("test.tar"))
    monkeypatch.setattr(files, "process", MagicMock(side_effect=process_side_effect))
    monkeypatch.setattr(files.time, "sleep", MagicMock(side_effect=KeyboardInterrupt))
    files.launch()


def test_launch_counts_success(monkeypatch):
    before = _labeled(metrics.FILES_PROCESSED_TOTAL, outcome="success")
    _run_launch_once(monkeypatch, process_side_effect=None)
    assert _labeled(metrics.FILES_PROCESSED_TOTAL, outcome="success") == before + 1


def test_launch_counts_no_device_config(monkeypatch):
    before = _labeled(metrics.FILES_PROCESSED_TOTAL, outcome="no_device_config")
    _run_launch_once(monkeypatch, process_side_effect=KeyError("no serial for dir"))
    assert (
        _labeled(metrics.FILES_PROCESSED_TOTAL, outcome="no_device_config")
        == before + 1
    )


def test_launch_counts_os_error(monkeypatch):
    before = _labeled(metrics.FILES_PROCESSED_TOTAL, outcome="os_error")
    _run_launch_once(monkeypatch, process_side_effect=OSError("device unreachable"))
    assert _labeled(metrics.FILES_PROCESSED_TOTAL, outcome="os_error") == before + 1


def test_launch_counts_unexpected_error(monkeypatch):
    before = _labeled(metrics.FILES_PROCESSED_TOTAL, outcome="unexpected_error")
    _run_launch_once(monkeypatch, process_side_effect=ValueError("boom"))
    assert (
        _labeled(metrics.FILES_PROCESSED_TOTAL, outcome="unexpected_error")
        == before + 1
    )


def test_launch_updates_pending_tars_after_failed_process(monkeypatch, tmp_path):
    # Regression: uploadrr_queue_depth alone can't show a backlog - a failed
    # process() leaves the queue immediately (queue.get() already dequeued the
    # item) even though the tar itself stays on disk for retry. pending_tars
    # must reflect that on-disk file, not the now-empty queue.
    (tmp_path / "test.tar").write_bytes(b"data")
    file_path = str(tmp_path / "test.tar")
    fake_config = MagicMock()
    fake_config.get_serial.return_value = "test_serial"
    monkeypatch.setattr(files, "CONFIG", fake_config)
    monkeypatch.setattr(files, "Observer", lambda: MagicMock())
    monkeypatch.setattr(files, "files_backfill", lambda q: q.put(file_path))
    monkeypatch.setattr(
        files, "process", MagicMock(side_effect=OSError("device unreachable"))
    )
    monkeypatch.setattr(files.time, "sleep", MagicMock(side_effect=KeyboardInterrupt))

    files.launch()

    assert metrics.PENDING_TARS.labels(serial="test_serial")._value.get() == 1
    assert metrics.QUEUE_DEPTH.collect()[0].samples[0].value == 0
