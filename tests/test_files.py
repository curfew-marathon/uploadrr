from queue import Empty
from unittest.mock import MagicMock

from uploadrr import adb, files, metrics, scan
from uploadrr.listener import MonitorFolder


def test_process_pushes_then_removes_source_file(monkeypatch, tmp_path):
    fake_config = MagicMock()
    fake_config.get_serial.return_value = "serial123"
    monkeypatch.setattr(scan, "CONFIG", fake_config)
    push_mock = MagicMock()
    monkeypatch.setattr(adb, "push_file", push_mock)
    tar_path = tmp_path / "x.tar"
    tar_path.write_bytes(b"data")

    files.process(str(tar_path))

    push_mock.assert_called_once_with("serial123", str(tar_path))
    assert not tar_path.exists()


def test_process_leaves_source_file_when_push_fails(monkeypatch, tmp_path):
    # A failed push must never reach os.remove - the caller relies on the
    # source tar staying on disk for retry.
    fake_config = MagicMock()
    fake_config.get_serial.return_value = "serial123"
    monkeypatch.setattr(scan, "CONFIG", fake_config)
    monkeypatch.setattr(
        adb, "push_file", MagicMock(side_effect=adb.AdbError("device unreachable"))
    )
    tar_path = tmp_path / "y.tar"
    tar_path.write_bytes(b"data")

    try:
        files.process(str(tar_path))
    except adb.AdbError:
        pass

    assert tar_path.exists()


def _labeled(counter, **labels):
    return counter.labels(**labels)._value.get()


def _run_launch_once(monkeypatch, process_side_effect):
    """Drive files.launch() through exactly one queued file, then exit the
    loop the same way it always exits: a KeyboardInterrupt. Observer/backfill
    are stubbed so this doesn't touch the filesystem or start real threads."""
    monkeypatch.setattr(files, "Observer", lambda: MagicMock())
    monkeypatch.setattr(scan, "files_backfill", lambda q: q.put("test.tar"))
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


def test_launch_discards_processed_marker_after_success(monkeypatch):
    # Once a file is fully processed, its entry in the de-dup set must be
    # cleared - otherwise a later on_closed() for a file of the same name
    # (e.g. after a device re-sends it) would be silently dropped forever.
    captured = {}

    def make_handler(queue):
        handler = MonitorFolder(queue)
        handler.processed_files.add("test.tar")
        captured["handler"] = handler
        return handler

    monkeypatch.setattr(files, "MonitorFolder", make_handler)
    monkeypatch.setattr(files, "Observer", lambda: MagicMock())
    monkeypatch.setattr(scan, "files_backfill", lambda q: q.put("test.tar"))
    monkeypatch.setattr(files, "process", MagicMock(return_value=None))
    monkeypatch.setattr(files.time, "sleep", MagicMock(side_effect=KeyboardInterrupt))

    files.launch()

    assert "test.tar" not in captured["handler"].processed_files


def test_launch_discards_processed_marker_after_failure(monkeypatch):
    # Regression: the discard used to only run in the success branch, so a
    # failed process() left the entry in processed_files indefinitely - a
    # same-path tar rewrite after the failure would then be silently dropped
    # by the listener's on_closed check until the next 24h scan, instead of
    # being picked up immediately.
    captured = {}

    def make_handler(queue):
        handler = MonitorFolder(queue)
        handler.processed_files.add("test.tar")
        captured["handler"] = handler
        return handler

    monkeypatch.setattr(files, "MonitorFolder", make_handler)
    monkeypatch.setattr(files, "Observer", lambda: MagicMock())
    monkeypatch.setattr(scan, "files_backfill", lambda q: q.put("test.tar"))
    monkeypatch.setattr(
        files, "process", MagicMock(side_effect=OSError("device unreachable"))
    )
    monkeypatch.setattr(files.time, "sleep", MagicMock(side_effect=KeyboardInterrupt))

    files.launch()

    assert "test.tar" not in captured["handler"].processed_files


def test_launch_updates_pending_tars_after_failed_process(monkeypatch, tmp_path):
    # Regression: uploadrr_queue_depth alone can't show a backlog - a failed
    # process() leaves the queue immediately (queue.get() already dequeued the
    # item) even though the tar itself stays on disk for retry. pending_tars
    # must reflect that on-disk file, not the now-empty queue.
    (tmp_path / "test.tar").write_bytes(b"data")
    file_path = str(tmp_path / "test.tar")
    fake_config = MagicMock()
    fake_config.get_serial.return_value = "test_serial"
    monkeypatch.setattr(scan, "CONFIG", fake_config)
    monkeypatch.setattr(files, "Observer", lambda: MagicMock())
    monkeypatch.setattr(scan, "files_backfill", lambda q: q.put(file_path))
    monkeypatch.setattr(
        files, "process", MagicMock(side_effect=OSError("device unreachable"))
    )
    monkeypatch.setattr(files.time, "sleep", MagicMock(side_effect=KeyboardInterrupt))

    files.launch()

    assert metrics.PENDING_TARS.labels(serial="test_serial")._value.get() == 1
    assert metrics.QUEUE_DEPTH.collect()[0].samples[0].value == 0


def _run_launch_through_periodic_scan(monkeypatch):
    """Drive files.launch() straight into the `except Empty:` periodic-scan
    branch (no file ever queued), then exit the loop the same way it always
    exits: a KeyboardInterrupt raised on the *next* queue.get() call."""
    fake_queue = MagicMock()
    fake_queue.qsize.return_value = 0
    fake_queue.get.side_effect = [Empty(), KeyboardInterrupt()]
    monkeypatch.setattr(files, "Queue", lambda: fake_queue)
    monkeypatch.setattr(files, "Observer", lambda: MagicMock())
    monkeypatch.setattr(scan, "files_backfill", MagicMock())
    fake_config = MagicMock()
    fake_config.get_data.return_value = [
        {"serial": "connected_serial"},
        {"serial": "disconnected_serial"},
    ]
    monkeypatch.setattr(scan, "CONFIG", fake_config)


def test_launch_records_device_connectivity_on_periodic_scan(monkeypatch):
    _run_launch_through_periodic_scan(monkeypatch)
    monkeypatch.setattr(
        adb, "connected_serials", MagicMock(return_value={"connected_serial"})
    )

    files.launch()

    assert _labeled(metrics.DEVICE_CONNECTED, serial="connected_serial") == 1
    assert _labeled(metrics.DEVICE_CONNECTED, serial="disconnected_serial") == 0


def test_launch_periodic_scan_survives_adb_server_down(monkeypatch):
    # Regression: the periodic scan branch must keep running its other work
    # (files_backfill already ran) and the loop must still exit cleanly even
    # when the adb server is unreachable - a metrics problem must never stall
    # or crash the transfer pipeline.
    _run_launch_through_periodic_scan(monkeypatch)
    monkeypatch.setattr(
        adb,
        "connected_serials",
        MagicMock(side_effect=adb.AdbError("adb server unreachable")),
    )
    before = _labeled(metrics.DEVICE_CONNECTED, serial="connected_serial")

    files.launch()  # must not raise

    assert _labeled(metrics.DEVICE_CONNECTED, serial="connected_serial") == before
