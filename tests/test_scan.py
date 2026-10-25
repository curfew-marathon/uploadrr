import os
from queue import Queue
from unittest.mock import MagicMock, patch

from uploadrr import metrics, scan
from uploadrr.scan import add_files


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
    with patch("uploadrr.scan.logger") as mock_logger:
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

    assert scan.count_pending_tars(str(tmp_path)) == 2


def test_count_pending_tars_missing_dir_returns_zero():
    assert scan.count_pending_tars("/non-existent-path") == 0


def test_add_files_logs_unresolvable_entry_type(tmp_path):
    broken_link = tmp_path / "broken"
    broken_link.symlink_to(tmp_path / "does-not-exist")
    queue = Queue()

    with patch("uploadrr.scan.logger") as mock_logger:
        add_files(str(tmp_path), queue)
        mock_logger.warning.assert_any_call(
            "Cannot resolve file type for: %s", str(broken_link)
        )
    assert queue.empty()


def test_add_files_logs_debug_when_no_tars_found(tmp_path):
    (tmp_path / "note.txt").write_text("data")
    queue = Queue()

    with patch("uploadrr.scan.logger") as mock_logger:
        add_files(str(tmp_path), queue)
        mock_logger.debug.assert_any_call("No tar files found in %s", str(tmp_path))
    assert queue.empty()


def test_add_files_logs_error_on_permission_denied(monkeypatch):
    def boom(path):
        raise PermissionError("denied")

    monkeypatch.setattr(scan.os, "listdir", boom)
    queue = Queue()

    with patch("uploadrr.scan.logger") as mock_logger:
        add_files("/some/path", queue)
        mock_logger.error.assert_called_once_with(
            "Permission denied accessing directory: %s", "/some/path"
        )
    assert queue.empty()


def test_add_files_logs_error_on_unexpected_listdir_failure(monkeypatch):
    def boom(path):
        raise NotADirectoryError("not a directory")

    monkeypatch.setattr(scan.os, "listdir", boom)
    queue = Queue()

    with patch("uploadrr.scan.logger") as mock_logger:
        add_files("/some/path", queue)
        mock_logger.error.assert_called_once()
    assert queue.empty()


def test_files_backfill_scans_each_configured_directory():
    fake_config = MagicMock()
    fake_config.get_data.return_value = [
        {"archive": "/archives/dev1"},
        {"archive": "/archives/dev2"},
    ]
    calls = []

    with (
        patch("uploadrr.scan.CONFIG", fake_config),
        patch("uploadrr.scan.add_files", lambda path, q: calls.append((path, q))),
    ):
        queue = Queue()
        scan.files_backfill(queue)

    assert calls == [("/archives/dev1", queue), ("/archives/dev2", queue)]


def test_add_files_sets_pending_tars_gauge(monkeypatch, tmp_path):
    (tmp_path / "a.tar").write_text("data")
    (tmp_path / "b.tar").write_text("data")
    fake_config = MagicMock()
    fake_config.get_serial.return_value = "test_serial"
    monkeypatch.setattr(scan, "CONFIG", fake_config)

    add_files(str(tmp_path), Queue())

    assert metrics.PENDING_TARS.labels(serial="test_serial")._value.get() == 2
