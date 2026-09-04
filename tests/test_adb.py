import io
import logging
import sys
import tarfile
import threading
import types
from unittest.mock import MagicMock

import pytest

sys.modules['ppadb'] = MagicMock()
sys.modules['ppadb.client'] = MagicMock()


class _FakeSyncConn:
    """Stands in for the ppadb Sync connection that `Device.push` owns."""

    def __init__(self, behavior, recorder):
        self.socket = MagicMock()
        self.behavior = behavior
        self.recorder = recorder
        self.closed = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def close(self):
        self.closed.set()


class _FakeSync:
    """Stands in for `ppadb.sync.Sync`, driven entirely through FakeRaw.sync()."""

    DEFAULT_CHMOD = 0o644

    def __init__(self, conn):
        self.conn = conn

    def push(self, src, dest, mode, progress=None):
        self.conn.recorder.append((src, dest))
        self.conn.behavior(self.conn, src, dest)


# `uploadrr.adb` imports `Sync` from `ppadb.sync` at module level, so this
# needs to be registered before `uploadrr.adb` is first imported below.
_fake_sync_module = types.ModuleType("ppadb.sync")
_fake_sync_module.Sync = _FakeSync  # type: ignore[attr-defined]
sys.modules["ppadb.sync"] = _fake_sync_module

from uploadrr import constants as C
from uploadrr.adb import (
    _RC_MARKER,
    AdbCommandError,
    AdbError,
    Device,
    _archive_members,
    _ensure_interactive,
    _interactive,
    _media_scan,
    get_device,
    pre_work,
    push_file,
    verify_free_space,
)

# The fakes above only existed to satisfy uploadrr.adb's own module-level
# imports; it now holds its own references (AdbClient, Sync), so don't leave
# process-global fakes in sys.modules for the rest of the pytest session -
# any test module that imports uploadrr.adb/.files/.config later, or a bare
# `import ppadb` anywhere else, should see the real package again.
del sys.modules["ppadb"]
del sys.modules["ppadb.client"]
del sys.modules["ppadb.sync"]

_SPLIT = "\necho " + _RC_MARKER

DF_OK = (
    "Filesystem     1K-blocks    Used  Available Use% Mounted on\n"
    "/dev/fuse      100000000    1000   99999000   1% /storage/emulated"
)
DF_LOW = (
    "Filesystem 1K-blocks Used Available Use% Mounted on\n"
    "/dev/fuse        512  384       128  75% /storage/emulated"
)


def _push_success(conn, src, dest):
    pass


def _push_stall_until_closed(conn, src, dest):
    while not conn.closed.wait(0.02):
        pass
    raise OSError("Bad file descriptor")


def _push_boom(conn, src, dest):
    raise RuntimeError("device offline")


class FakeRaw:
    """Stand-in for a ppadb device: records shell calls, replays canned output."""

    def __init__(self, responses=None, push_behavior=_push_success):
        self.serial = "test_serial"
        self.shell_calls = []
        self.pushed = []
        self.sync_conns = []
        self._responses = responses or {}
        self.push_behavior = push_behavior

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

    def sync(self):
        conn = _FakeSyncConn(self.push_behavior, self.pushed)
        self.sync_conns.append(conn)
        return conn


def _make_tar(tmp_path, name, members):
    """Build a local tar. `members` is (relpath, data) pairs; data=None writes a
    symlink to /etc/passwd instead of a regular file."""
    path = tmp_path / name
    with tarfile.open(path, "w") as tar:
        for rel, data in members:
            info = tarfile.TarInfo(rel)
            if data is None:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return path


# --- Device.sh ---------------------------------------------------------------

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


# --- Device.push ---------------------------------------------------------------

def test_push_success_records_transfer(tmp_path):
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw()
    Device(raw).push(str(f), "/sdcard/Download/a.tar")
    assert raw.pushed == [(str(f), "/sdcard/Download/a.tar")]


def test_push_sets_socket_timeout_for_stall_detection(tmp_path):
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw()
    Device(raw).push(str(f), "/sdcard/Download/a.tar", stall_timeout=42)
    raw.sync_conns[0].socket.settimeout.assert_called_once_with(42)


def test_push_closes_connection_and_raises_when_worker_never_finishes(
    monkeypatch, tmp_path
):
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    monkeypatch.setattr(C, "PUSH_TIMEOUT_FLOOR", 0.2)
    monkeypatch.setattr(C, "PUSH_MIN_BYTES_PER_SEC", 10**12)
    raw = FakeRaw(push_behavior=_push_stall_until_closed)

    with pytest.raises(AdbError, match="exceeded"):
        Device(raw).push(str(f), "/sdcard/Download/a.tar")

    # The stalled worker was forced dead, not abandoned, before push() raised.
    assert raw.sync_conns[0].closed.is_set()


def test_push_propagates_worker_error(tmp_path):
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw(push_behavior=_push_boom)
    with pytest.raises(AdbError, match="failed"):
        Device(raw).push(str(f), "/sdcard/Download/a.tar")


def test_push_wraps_sync_setup_failure_as_adberror(tmp_path):
    # raw.sync() itself can raise (e.g. the sync: handshake fails) before any
    # connection object exists - this must not leak a raw RuntimeError, which
    # files.py's `except OSError` handler wouldn't catch.
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw()

    def boom_sync():
        raise RuntimeError("adb server closed the connection")

    raw.sync = boom_sync
    with pytest.raises(AdbError, match="could not start"):
        Device(raw).push(str(f), "/sdcard/Download/a.tar")


def test_push_closes_partial_connection_when_configuring_it_fails(tmp_path):
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw()
    conn = _FakeSyncConn(_push_success, raw.pushed)
    conn.socket.settimeout.side_effect = OSError("bad file descriptor")
    raw.sync = lambda: conn

    with pytest.raises(AdbError, match="could not start"):
        Device(raw).push(str(f), "/sdcard/Download/a.tar")

    assert conn.closed.is_set()


# --- get_device / _client ----------------------------------------------------

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


def test_get_device_timeout_becomes_adberror(monkeypatch):
    client = MagicMock()
    client.device.side_effect = TimeoutError("timed out")
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


def test_client_wraps_create_connection_with_default_timeout(monkeypatch):
    """ppadb's host commands (devices()/device()) call create_connection() with
    no timeout; `_client()` must default that so discovery can't hang."""
    calls = []

    class DummyClient:
        def create_connection(self, timeout=None):
            calls.append(timeout)
            return "conn"

    monkeypatch.setattr("uploadrr.adb.AdbClient", lambda host, port: DummyClient())
    monkeypatch.setattr("uploadrr.adb._CLIENT", None)

    from uploadrr.adb import _client

    client = _client()
    client.create_connection()
    client.create_connection(timeout=5)

    assert calls == [C.CONNECT_TIMEOUT, 5]


# --- verify_free_space --------------------------------------------------------

def test_verify_free_space_pass():
    verify_free_space(Device(FakeRaw({"df -k": (DF_OK, 0)})), 1000)


def test_verify_free_space_insufficient():
    with pytest.raises(AdbError, match="insufficient free space"):
        verify_free_space(Device(FakeRaw({"df -k": (DF_LOW, 0)})), 100_000)


def test_verify_free_space_unparseable():
    with pytest.raises(AdbError):
        verify_free_space(Device(FakeRaw({"df -k": ("one line only", 0)})), 1)


# --- _archive_members ---------------------------------------------------------

def test_archive_members_returns_regular_file_names(tmp_path):
    tar = _make_tar(tmp_path, "a.tar", [("a.jpg", b"1"), ("dir/b.jpg", b"22")])
    assert _archive_members(str(tar)) == ["a.jpg", "dir/b.jpg"]


@pytest.mark.parametrize("bad_name", ["../x.jpg", "dir/../../x.jpg", ".."])
def test_archive_members_rejects_parent_dir_components(tmp_path, bad_name):
    tar = _make_tar(tmp_path, "a.tar", [(bad_name, b"1")])
    with pytest.raises(AdbError, match="unsafe path"):
        _archive_members(str(tar))


def test_archive_members_rejects_absolute_path(tmp_path):
    tar = _make_tar(tmp_path, "a.tar", [("/etc/passwd", b"1")])
    with pytest.raises(AdbError, match="unsafe path"):
        _archive_members(str(tar))


def test_archive_members_rejects_symlink(tmp_path):
    tar = _make_tar(tmp_path, "a.tar", [("link", None)])
    with pytest.raises(AdbError, match="link or special file"):
        _archive_members(str(tar))


def test_archive_members_rejects_empty_archive(tmp_path):
    path = tmp_path / "empty.tar"
    with tarfile.open(path, "w"):
        pass
    with pytest.raises(AdbError, match="no regular files"):
        _archive_members(str(path))


# --- pre_work / push_file -----------------------------------------------------

def test_pre_work_does_not_touch_device():
    raw = FakeRaw()
    pre_work(Device(raw))
    assert raw.shell_calls == []


def test_push_file_raises_and_keeps_source_when_extract_fails(monkeypatch, tmp_path):
    tar = _make_tar(tmp_path, "b.tar", [("p1.jpg", b"data1"), ("p2.jpg", b"data2")])
    raw = FakeRaw({"df -k": (DF_OK, 0), "tar -xf": ("tar: write error", 1)})
    monkeypatch.setattr("uploadrr.adb.get_device", lambda s: Device(raw))
    post = MagicMock()
    monkeypatch.setattr("uploadrr.adb.post_work", post)

    with pytest.raises(AdbCommandError):
        push_file("test_serial", str(tar))

    assert tar.exists()  # caller decides deletion; push_file never removed it
    assert post.call_count == 0
    assert any(c.startswith("rm -f") for c in raw.shell_calls)  # finally cleanup ran


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
    monkeypatch.setattr("uploadrr.adb.get_device", lambda s: Device(raw))
    monkeypatch.setattr("uploadrr.adb.post_work", MagicMock())

    push_file("test_serial", str(tar))  # cleanup failure must not raise

    assert "Could not remove" in caplog.text


def test_push_file_happy_path_scans_extracted_files(monkeypatch, tmp_path):
    tar = _make_tar(
        tmp_path, "c.tar", [("dir/p1.jpg", b"data1"), ("dir/p2.jpg", b"data2")]
    )
    raw = FakeRaw({"df -k": (DF_OK, 0)})
    monkeypatch.setattr("uploadrr.adb.get_device", lambda s: Device(raw))
    captured = {}
    monkeypatch.setattr(
        "uploadrr.adb.post_work",
        lambda d, paths: captured.setdefault("paths", paths),
    )

    push_file("test_serial", str(tar))

    assert raw.pushed == [(str(tar), "/sdcard/Download/c.tar")]
    assert captured["paths"] == [
        "/sdcard/DCIM/dir/p1.jpg",
        "/sdcard/DCIM/dir/p2.jpg",
    ]
    assert any(c.startswith("mkdir -p") for c in raw.shell_calls)
    assert any(c.startswith("rm -f") for c in raw.shell_calls)
    assert not any(c.startswith("tar -tf") for c in raw.shell_calls)


def test_push_file_creates_camera_dir_before_checking_free_space(monkeypatch, tmp_path):
    # `df` on a path that doesn't exist yet fails, so mkdir -p must run first -
    # otherwise a fresh device (no /sdcard/DCIM yet) could never get past this.
    tar = _make_tar(tmp_path, "e.tar", [("p1.jpg", b"data")])
    raw = FakeRaw({"df -k": (DF_OK, 0)})
    monkeypatch.setattr("uploadrr.adb.get_device", lambda s: Device(raw))
    monkeypatch.setattr("uploadrr.adb.post_work", MagicMock())

    push_file("test_serial", str(tar))

    mkdir_index = next(
        i for i, c in enumerate(raw.shell_calls) if c.startswith("mkdir -p")
    )
    df_index = next(i for i, c in enumerate(raw.shell_calls) if c.startswith("df -k"))
    assert mkdir_index < df_index


# --- post_work helpers ---------------------------------------------------------

def test_media_scan_batches_and_joins_with_and():
    raw = FakeRaw()
    _media_scan(Device(raw), [f"/sdcard/DCIM/p{i}.jpg" for i in range(5)], batch=2)
    assert len(raw.shell_calls) == 3  # ceil(5 / 2)
    broadcasts = sum(c.count("MEDIA_SCANNER_SCAN_FILE") for c in raw.shell_calls)
    assert broadcasts == 5
    two_file_calls = [c for c in raw.shell_calls if c.count("MEDIA_SCANNER_SCAN_FILE") == 2]
    assert two_file_calls and all(" && " in c for c in two_file_calls)


def test_media_scan_noop_without_paths():
    raw = FakeRaw()
    _media_scan(Device(raw), [])
    assert raw.shell_calls == []


def test_media_scan_percent_encodes_reserved_uri_characters():
    raw = FakeRaw()
    _media_scan(Device(raw), ["/sdcard/DCIM/a#b.jpg", "/sdcard/DCIM/c?d.jpg"], batch=2)
    cmd = raw.shell_calls[0]
    assert "file:///sdcard/DCIM/a%23b.jpg" in cmd
    assert "file:///sdcard/DCIM/c%3Fd.jpg" in cmd


def test_media_scan_propagates_broadcast_failure():
    raw = FakeRaw({"am broadcast": ("Broadcast completed: result=0", 1)})
    with pytest.raises(AdbCommandError):
        _media_scan(Device(raw), ["/sdcard/DCIM/p1.jpg"])


def test_interactive_true_only_on_affirmative_signals():
    raw = FakeRaw(
        {
            "dumpsys power": ("mWakefulness=Awake", 0),
            "dumpsys window": ("mShowingLockscreen=false", 0),
        }
    )
    assert _interactive(Device(raw)) is True


def test_interactive_treats_unknown_keyguard_output_as_locked():
    # Screen is awake but the keyguard grep matches nothing (renamed field,
    # unsupported dumpsys version, ...) - must not be read as "unlocked".
    raw = FakeRaw({"dumpsys power": ("mWakefulness=Awake", 0)})
    assert _interactive(Device(raw)) is False


def test_interactive_false_when_asleep_even_if_keyguard_clear():
    raw = FakeRaw({"dumpsys window": ("mShowingLockscreen=false", 0)})
    assert _interactive(Device(raw)) is False


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
    raw = FakeRaw()  # every dumpsys returns "" -> never interactive, never awake
    _ensure_interactive(Device(raw), settle=0)
    joined = " ".join(raw.shell_calls)
    assert "KEYCODE_WAKEUP" in joined
    assert "dismiss-keyguard" in joined
    assert "keyevent 82" in joined
    assert "input touchscreen swipe" in joined
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("not confirmed interactive" in r.message for r in warnings)


def test_ensure_interactive_logs_info_when_awake_but_keyguard_unconfirmed(caplog):
    caplog.set_level(logging.INFO)
    # Awake, but the keyguard grep never matches - stays unconfirmed, not locked.
    raw = FakeRaw({"dumpsys power": ("mWakefulness=Awake", 0)})
    _ensure_interactive(Device(raw), settle=0)
    matches = [r for r in caplog.records if "not confirmed interactive" in r.message]
    assert matches
    assert all(r.levelno == logging.INFO for r in matches)


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
