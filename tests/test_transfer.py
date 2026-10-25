import logging
from unittest.mock import MagicMock

import pytest
from conftest import (
    DF_LOW,
    DF_NON_NUMERIC_AVAILABLE,
    DF_OK,
    DF_TOO_FEW_COLUMNS,
    FakeRaw,
    _histogram_count,
    _labeled,
    _make_tar,
)

from uploadrr import metrics
from uploadrr.adb.transfer import (
    post_work,
    pre_work,
    push_file,
    verify_free_space,
)
from uploadrr.adb.transport import AdbCommandError, AdbError, Device

pytestmark = pytest.mark.usefixtures("_patch_sync")


# --- verify_free_space --------------------------------------------------------


def test_verify_free_space_pass():
    verify_free_space(Device(FakeRaw({"df -k": (DF_OK, 0)})), 1000)


def test_verify_free_space_insufficient():
    with pytest.raises(AdbError, match="insufficient free space"):
        verify_free_space(Device(FakeRaw({"df -k": (DF_LOW, 0)})), 100_000)


def test_verify_free_space_unparseable():
    with pytest.raises(AdbError):
        verify_free_space(Device(FakeRaw({"df -k": ("one line only", 0)})), 1)


def test_verify_free_space_too_few_columns_raises_cannot_parse():
    with pytest.raises(AdbError, match="Cannot parse"):
        verify_free_space(Device(FakeRaw({"df -k": (DF_TOO_FEW_COLUMNS, 0)})), 1)


def test_verify_free_space_non_numeric_available_raises_cannot_parse():
    with pytest.raises(AdbError, match="Cannot parse"):
        verify_free_space(Device(FakeRaw({"df -k": (DF_NON_NUMERIC_AVAILABLE, 0)})), 1)


def test_verify_free_space_records_gauge_on_pass():
    raw = FakeRaw({"df -k": (DF_OK, 0)})
    verify_free_space(Device(raw), 1000)
    assert _labeled(metrics.DEVICE_FREE_BYTES, serial=raw.serial) == 99999000 * 1024


def test_verify_free_space_records_gauge_before_raising():
    # The gate check parses free space before it knows whether it's enough -
    # the reading is still worth recording even on the failing path.
    raw = FakeRaw({"df -k": (DF_LOW, 0)})
    with pytest.raises(AdbError, match="insufficient free space"):
        verify_free_space(Device(raw), 100_000)
    assert _labeled(metrics.DEVICE_FREE_BYTES, serial=raw.serial) == 128 * 1024


# --- pre_work / push_file -----------------------------------------------------


def test_pre_work_does_not_touch_device():
    raw = FakeRaw()
    pre_work(Device(raw))
    assert raw.shell_calls == []


def test_push_file_raises_and_keeps_source_when_extract_fails(monkeypatch, tmp_path):
    tar = _make_tar(tmp_path, "b.tar", [("p1.jpg", b"data1"), ("p2.jpg", b"data2")])
    raw = FakeRaw({"df -k": (DF_OK, 0), "tar -xf": ("tar: write error", 1)})
    monkeypatch.setattr("uploadrr.adb.transport.get_device", lambda s: Device(raw))
    post = MagicMock()
    monkeypatch.setattr("uploadrr.adb.transfer.post_work", post)
    before = _labeled(metrics.PUSH_FAILURES_TOTAL, serial=raw.serial)

    with pytest.raises(AdbCommandError):
        push_file("test_serial", str(tar))

    assert tar.exists()  # caller decides deletion; push_file never removed it
    assert post.call_count == 0
    assert any(c.startswith("rm -f") for c in raw.shell_calls)  # finally cleanup ran
    assert _labeled(metrics.PUSH_FAILURES_TOTAL, serial=raw.serial) == before + 1


def test_push_file_rejects_unsafe_archive_before_touching_device(tmp_path):
    tar = _make_tar(tmp_path, "b.tar", [("../../etc/hosts", b"x")])
    with pytest.raises(AdbError, match="unsafe path"):
        push_file("test_serial", str(tar))


def test_push_file_rejects_symlink_archive_before_touching_device(tmp_path):
    tar = _make_tar(tmp_path, "b.tar", [("link", None)])
    with pytest.raises(AdbError, match="link or special file"):
        push_file("test_serial", str(tar))


def test_push_file_logs_warning_when_cleanup_fails(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    tar = _make_tar(tmp_path, "d.tar", [("p1.jpg", b"data")])
    raw = FakeRaw({"df -k": (DF_OK, 0), "rm -f": ("Permission denied", 1)})
    monkeypatch.setattr("uploadrr.adb.transport.get_device", lambda s: Device(raw))
    monkeypatch.setattr("uploadrr.adb.transfer.post_work", MagicMock())

    push_file("test_serial", str(tar))  # cleanup failure must not raise

    assert "Could not remove" in caplog.text


def test_push_file_happy_path_scans_extracted_files(monkeypatch, tmp_path):
    tar = _make_tar(
        tmp_path, "c.tar", [("dir/p1.jpg", b"data1"), ("dir/p2.jpg", b"data2")]
    )
    raw = FakeRaw({"df -k": (DF_OK, 0)})
    monkeypatch.setattr("uploadrr.adb.transport.get_device", lambda s: Device(raw))
    captured = {}
    monkeypatch.setattr(
        "uploadrr.adb.transfer.post_work",
        lambda d, paths: captured.setdefault("paths", paths),
    )
    before_seconds = _histogram_count(metrics.PUSH_SECONDS, serial=raw.serial)
    before_failures = _labeled(metrics.PUSH_FAILURES_TOTAL, serial=raw.serial)

    push_file("test_serial", str(tar))

    assert raw.pushed == [(str(tar), "/sdcard/Download/c.tar")]
    assert captured["paths"] == [
        "/sdcard/DCIM/dir/p1.jpg",
        "/sdcard/DCIM/dir/p2.jpg",
    ]
    assert any(c.startswith("mkdir -p") for c in raw.shell_calls)
    assert any(c.startswith("rm -f") for c in raw.shell_calls)
    assert not any(c.startswith("tar -tf") for c in raw.shell_calls)
    assert (
        _histogram_count(metrics.PUSH_SECONDS, serial=raw.serial) == before_seconds + 1
    )
    # A successful push must not also count as a failure.
    assert _labeled(metrics.PUSH_FAILURES_TOTAL, serial=raw.serial) == before_failures


def test_push_file_records_free_space_at_both_points(monkeypatch, tmp_path):
    tar = _make_tar(tmp_path, "i.tar", [("p1.jpg", b"data")])
    raw = FakeRaw({"df -k": (DF_OK, 0)})
    monkeypatch.setattr("uploadrr.adb.transport.get_device", lambda s: Device(raw))
    monkeypatch.setattr("uploadrr.adb.transfer.post_work", MagicMock())

    push_file("test_serial", str(tar))

    df_calls = [c for c in raw.shell_calls if c.startswith("df -k")]
    assert len(df_calls) == 2  # pre-push gate check, post-cleanup read
    assert _labeled(metrics.DEVICE_FREE_BYTES, serial=raw.serial) == 99999000 * 1024


def test_push_file_post_cleanup_free_space_read_failure_does_not_raise(
    monkeypatch, tmp_path, caplog
):
    # The transfer already succeeded by the time the post-cleanup read runs,
    # so a failure there is informational only - it must never surface as a
    # raised error from a successful push.
    caplog.set_level(logging.WARNING)
    tar = _make_tar(tmp_path, "j.tar", [("p1.jpg", b"data")])
    raw = FakeRaw({"df -k": (DF_OK, 0)})
    monkeypatch.setattr("uploadrr.adb.transport.get_device", lambda s: Device(raw))
    monkeypatch.setattr("uploadrr.adb.transfer.post_work", MagicMock())

    calls = {"n": 0}

    def flaky_free_bytes(device):
        calls["n"] += 1
        if calls["n"] == 1:
            return 99999000 * 1024
        raise AdbError("device offline")

    monkeypatch.setattr("uploadrr.adb.transfer._free_bytes", flaky_free_bytes)

    push_file("test_serial", str(tar))  # must not raise

    assert "Could not read free space" in caplog.text


def test_push_file_counts_failure_when_post_work_raises(monkeypatch, tmp_path):
    # A media-scan dispatch failure (surfaced via post_work) happens after a
    # successful push+extract, but must still count as a transfer failure -
    # otherwise it's invisible in metrics even though the caller keeps the
    # source archive for retry.
    tar = _make_tar(tmp_path, "f.tar", [("p1.jpg", b"data")])
    raw = FakeRaw({"df -k": (DF_OK, 0)})
    monkeypatch.setattr("uploadrr.adb.transport.get_device", lambda s: Device(raw))
    monkeypatch.setattr(
        "uploadrr.adb.transfer.post_work",
        MagicMock(side_effect=AdbCommandError("am broadcast", 1, "failed")),
    )
    before = _labeled(metrics.PUSH_FAILURES_TOTAL, serial=raw.serial)

    with pytest.raises(AdbCommandError):
        push_file("test_serial", str(tar))

    assert tar.exists()  # caller decides deletion; push_file never removed it
    assert _labeled(metrics.PUSH_FAILURES_TOTAL, serial=raw.serial) == before + 1


def test_push_file_counts_failure_when_get_device_raises(monkeypatch, tmp_path):
    # No Device object exists yet when get_device() itself fails (device
    # unplugged, adb server down, ...), so the failure counter must use the
    # input `serial` directly rather than `device.serial`.
    tar = _make_tar(tmp_path, "g.tar", [("p1.jpg", b"data")])
    monkeypatch.setattr(
        "uploadrr.adb.transport.get_device",
        MagicMock(side_effect=AdbError("adb server unreachable for test_serial")),
    )
    before = _labeled(metrics.PUSH_FAILURES_TOTAL, serial="test_serial")

    with pytest.raises(AdbError, match="unreachable"):
        push_file("test_serial", str(tar))

    assert tar.exists()
    assert _labeled(metrics.PUSH_FAILURES_TOTAL, serial="test_serial") == before + 1


def test_push_file_counts_failure_when_free_space_check_raises(monkeypatch, tmp_path):
    tar = _make_tar(tmp_path, "h.tar", [("p1.jpg", b"x" * 100_000)])
    raw = FakeRaw({"df -k": (DF_LOW, 0)})
    monkeypatch.setattr("uploadrr.adb.transport.get_device", lambda s: Device(raw))
    before = _labeled(metrics.PUSH_FAILURES_TOTAL, serial=raw.serial)

    with pytest.raises(AdbError, match="insufficient free space"):
        push_file("test_serial", str(tar))

    assert _labeled(metrics.PUSH_FAILURES_TOTAL, serial=raw.serial) == before + 1


def test_push_file_creates_camera_dir_before_checking_free_space(monkeypatch, tmp_path):
    # `df` on a path that doesn't exist yet fails, so mkdir -p must run first -
    # otherwise a fresh device (no /sdcard/DCIM yet) could never get past this.
    tar = _make_tar(tmp_path, "e.tar", [("p1.jpg", b"data")])
    raw = FakeRaw({"df -k": (DF_OK, 0)})
    monkeypatch.setattr("uploadrr.adb.transport.get_device", lambda s: Device(raw))
    monkeypatch.setattr("uploadrr.adb.transfer.post_work", MagicMock())

    push_file("test_serial", str(tar))

    mkdir_index = next(
        i for i, c in enumerate(raw.shell_calls) if c.startswith("mkdir -p")
    )
    df_index = next(i for i, c in enumerate(raw.shell_calls) if c.startswith("df -k"))
    assert mkdir_index < df_index


# --- post_work -----------------------------------------------------------------


def test_post_work_wakes_scans_and_nudges_in_order():
    raw = FakeRaw(
        {
            "dumpsys power": ("mWakefulness=Awake", 0),
            "dumpsys window": ("mShowingLockscreen=false", 0),
            "dumpsys user": ("mRunningUnlocked=true", 0),
        }
    )
    post_work(Device(raw), ["/sdcard/DCIM/p1.jpg"])

    unforce_i = next(
        i for i, c in enumerate(raw.shell_calls) if "deviceidle unforce" in c
    )
    scan_i = next(
        i for i, c in enumerate(raw.shell_calls) if "MEDIA_SCANNER_SCAN_FILE" in c
    )
    monkey_i = next(i for i, c in enumerate(raw.shell_calls) if "monkey" in c)
    assert unforce_i < scan_i < monkey_i
