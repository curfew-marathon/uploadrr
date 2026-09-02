import logging
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.modules['ppadb'] = MagicMock()
sys.modules['ppadb.client'] = MagicMock()

from uploadrr import constants as C
from uploadrr.adb import (
    _RC_MARKER,
    AdbCommandError,
    AdbError,
    Device,
    _ensure_interactive,
    _media_scan,
    _reject_unsafe_paths,
    get_device,
    pre_work,
    push_file,
    verify_free_space,
)

_SPLIT = "\necho " + _RC_MARKER

DF_OK = (
    "Filesystem     1K-blocks    Used  Available Use% Mounted on\n"
    "/dev/fuse      100000000    1000   99999000   1% /storage/emulated"
)
DF_LOW = (
    "Filesystem 1K-blocks Used Available Use% Mounted on\n"
    "/dev/fuse        512  384       128  75% /storage/emulated"
)


class FakeRaw:
    """Stand-in for a ppadb device: records shell calls, replays canned output."""

    def __init__(self, responses=None):
        self.serial = "test_serial"
        self.shell_calls = []
        self.pushed = []
        self._responses = responses or {}

    def shell(self, cmd, timeout=None):
        real = cmd.rsplit(_SPLIT, 1)[0]
        self.shell_calls.append(real)
        body, rc = "", 0
        for needle, value in self._responses.items():
            if needle in real:
                body, rc = value if isinstance(value, tuple) else (value, 0)
                break
        sep = "\n" if body else ""
        return f"{body}{sep}{_RC_MARKER}{rc}\n"

    def push(self, src, dest, progress=None):
        self.pushed.append((src, dest))
        if progress is not None:
            size = 4096
            progress(src, size, size)


# --- Device.sh --------------------------------------------------------------

def test_sh_returns_body_without_marker():
    dev = Device(FakeRaw({"echo hi": ("hi", 0)}))
    assert dev.sh("echo hi").strip() == "hi"


def test_sh_raises_on_nonzero_exit():
    dev = Device(FakeRaw({"tar -xf": ("tar: short read", 1)}))
    with pytest.raises(AdbCommandError) as exc:
        dev.sh("tar -xf a -C b")
    assert exc.value.rc == 1


def test_sh_check_false_swallows_nonzero():
    dev = Device(FakeRaw({"grep": ("", 1)}))
    assert dev.sh("grep x", check=False) == ""


def test_sh_missing_marker_raises_adberror():
    raw = FakeRaw()
    raw.shell = lambda cmd, timeout=None: "no marker in here"
    with pytest.raises(AdbError, match="exit marker"):
        Device(raw).sh("whatever")


def test_sh_transport_error_becomes_adberror():
    raw = FakeRaw()

    def boom(cmd, timeout=None):
        raise RuntimeError("connection reset by peer")

    raw.shell = boom
    with pytest.raises(AdbError):
        Device(raw).sh("df")


# --- Device.push -----------------------------------------------------------

def test_push_success_records_transfer(tmp_path):
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw()
    Device(raw).push(str(f), "/sdcard/Download/a.tar")
    assert raw.pushed == [(str(f), "/sdcard/Download/a.tar")]


def test_push_aborts_on_stall(tmp_path):
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw()
    raw.push = lambda src, dest, progress=None: time.sleep(3)  # never reports progress
    with pytest.raises(AdbError, match="stalled"):
        Device(raw).push(str(f), "/sdcard/Download/a.tar", stall_timeout=0.3)


def test_push_aborts_when_too_slow_overall(monkeypatch, tmp_path):
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    monkeypatch.setattr(C, "PUSH_TIMEOUT_FLOOR", 0)
    monkeypatch.setattr(C, "PUSH_MIN_BYTES_PER_SEC", 10**12)  # hard deadline ~= now

    def crawl(src, dest, progress=None):
        for i in range(10):
            time.sleep(0.05)
            progress(src, 4096, (i + 1) * 400)

    raw = FakeRaw()
    raw.push = crawl
    with pytest.raises(AdbError, match="below"):
        Device(raw).push(str(f), "/sdcard/Download/a.tar", stall_timeout=30)


def test_push_propagates_worker_error(tmp_path):
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw()

    def boom(src, dest, progress=None):
        raise RuntimeError("device offline")

    raw.push = boom
    with pytest.raises(AdbError, match="failed"):
        Device(raw).push(str(f), "/sdcard/Download/a.tar")


# --- get_device ----------------------------------------------------------------

def test_get_device_not_connected(monkeypatch):
    client = MagicMock()
    client.device.return_value = None
    monkeypatch.setattr("uploadrr.adb._CLIENT", client)
    with pytest.raises(AdbError, match="not connected"):
        get_device("test_serial")


def test_get_device_server_down(monkeypatch):
    client = MagicMock()
    client.device.side_effect = RuntimeError("Is adb running on your computer?")
    monkeypatch.setattr("uploadrr.adb._CLIENT", client)
    with pytest.raises(AdbError, match="adb server unreachable"):
        get_device("test_serial")


def test_get_device_success(monkeypatch):
    raw = FakeRaw()
    client = MagicMock()
    client.device.return_value = raw
    monkeypatch.setattr("uploadrr.adb._CLIENT", client)
    dev = get_device("test_serial")
    assert isinstance(dev, Device)
    assert dev.serial == "test_serial"


# --- verify_free_space -------------------------------------------------------

def test_verify_free_space_pass():
    verify_free_space(Device(FakeRaw({"df -k": (DF_OK, 0)})), 1000)


def test_verify_free_space_insufficient():
    with pytest.raises(AdbError, match="insufficient free space"):
        verify_free_space(Device(FakeRaw({"df -k": (DF_LOW, 0)})), 100_000)


def test_verify_free_space_unparseable():
    with pytest.raises(AdbError):
        verify_free_space(Device(FakeRaw({"df -k": ("one line only", 0)})), 1)


# --- pre_work / push_file ---------------------------------------------------

def test_pre_work_does_not_touch_device():
    raw = FakeRaw()
    pre_work(Device(raw))
    assert raw.shell_calls == []


def test_push_file_raises_and_keeps_source_when_extract_fails(monkeypatch, tmp_path):
    f = tmp_path / "b.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw(
        {
            "df -k": (DF_OK, 0),
            "tar -tf": ("p1.jpg\np2.jpg", 0),
            "tar -xf": ("tar: write error", 1),
        }
    )
    monkeypatch.setattr("uploadrr.adb.get_device", lambda s: Device(raw))
    post = MagicMock()
    monkeypatch.setattr("uploadrr.adb.post_work", post)

    with pytest.raises(AdbCommandError):
        push_file("test_serial", str(f))

    assert f.exists()  # caller decides deletion; push_file never removed it
    assert post.call_count == 0
    assert any(c.startswith("rm -f") for c in raw.shell_calls)  # finally cleanup ran


def test_push_file_rejects_unsafe_archive(monkeypatch, tmp_path):
    f = tmp_path / "b.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw(
        {
            "df -k": (DF_OK, 0),
            "tar -tf": ("../../etc/hosts", 0),
        }
    )
    monkeypatch.setattr("uploadrr.adb.get_device", lambda s: Device(raw))
    monkeypatch.setattr("uploadrr.adb.post_work", MagicMock())
    with pytest.raises(AdbError, match="unsafe path"):
        push_file("test_serial", str(f))
    assert not any(c.startswith("tar -xf") for c in raw.shell_calls)


def test_push_file_happy_path_scans_extracted_files(monkeypatch, tmp_path):
    f = tmp_path / "c.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw(
        {
            "df -k": (DF_OK, 0),
            "tar -tf": ("dir/\ndir/p1.jpg\ndir/p2.jpg", 0),
        }
    )
    monkeypatch.setattr("uploadrr.adb.get_device", lambda s: Device(raw))
    captured = {}
    monkeypatch.setattr(
        "uploadrr.adb.post_work",
        lambda d, paths: captured.setdefault("paths", paths),
    )

    push_file("test_serial", str(f))

    assert raw.pushed == [(str(f), "/sdcard/Download/c.tar")]
    assert captured["paths"] == [
        "/sdcard/DCIM/dir/p1.jpg",
        "/sdcard/DCIM/dir/p2.jpg",
    ]
    assert any(c.startswith("mkdir -p") for c in raw.shell_calls)
    assert any(c.startswith("rm -f") for c in raw.shell_calls)


# --- post_work helpers ----------------------------------------------------------

def test_media_scan_one_broadcast_per_file():
    raw = FakeRaw()
    _media_scan(Device(raw), [f"/sdcard/DCIM/p{i}.jpg" for i in range(5)], batch=2)
    broadcasts = sum(c.count("MEDIA_SCANNER_SCAN_FILE") for c in raw.shell_calls)
    assert broadcasts == 5
    assert len(raw.shell_calls) == 3  # ceil(5 / 2)


def test_media_scan_noop_without_paths():
    raw = FakeRaw()
    _media_scan(Device(raw), [])
    assert raw.shell_calls == []


def test_ensure_interactive_no_action_when_already_up():
    raw = FakeRaw(
        {
            "dumpsys power": ("mWakefulness=Awake", 0),
            "dumpsys window": ("mShowingLockscreen=false", 0),
            "dumpsys user": ("  mRunningUnlocked=true", 0),
        }
    )
    _ensure_interactive(Device(raw), settle=0)
    assert not any("keyevent" in c for c in raw.shell_calls)


def test_ensure_interactive_escalates_then_warns(caplog):
    caplog.set_level(logging.WARNING)
    raw = FakeRaw()  # every dumpsys returns "" -> never interactive
    _ensure_interactive(Device(raw), settle=0)
    joined = " ".join(raw.shell_calls)
    assert "KEYCODE_WAKEUP" in joined
    assert "dismiss-keyguard" in joined
    assert "keyevent 82" in joined
    assert "input touchscreen swipe" in joined
    assert "not confirmed interactive" in caplog.text


def test_ensure_interactive_warns_on_fbe_locked(caplog):
    caplog.set_level(logging.WARNING)
    raw = FakeRaw(
        {
            "dumpsys power": ("mWakefulness=Awake", 0),
            "dumpsys window": ("mShowingLockscreen=false", 0),
            "dumpsys user": ("mRunningUnlocked=false", 0),
        }
    )
    _ensure_interactive(Device(raw), settle=0)
    assert "not been unlocked since boot" in caplog.text


# --- _reject_unsafe_paths ---------------------------------------------------

def test_reject_unsafe_paths():
    _reject_unsafe_paths(["a/b.jpg", "c.jpg"])  # no raise
    for bad in (["../x"], ["/etc/passwd"], ["a/../../x"]):
        with pytest.raises(AdbError):
            _reject_unsafe_paths(bad)
