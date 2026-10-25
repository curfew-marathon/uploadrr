import logging
import threading
from unittest.mock import MagicMock

import pytest
from conftest import FakeRaw, _FakeShellConn, _FakeSyncConn, _labeled

from uploadrr import constants as C
from uploadrr import metrics
from uploadrr.adb.transport import (
    AdbCommandError,
    AdbError,
    Device,
    connected_serials,
    get_device,
)

pytestmark = pytest.mark.usefixtures("_patch_sync")


def _push_success(conn, src, dest):
    pass


def _push_stall_until_closed(conn, src, dest):
    while not conn.closed.wait(0.02):
        pass
    raise OSError("Bad file descriptor")


def _push_boom(conn, src, dest):
    raise RuntimeError("device offline")


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

    def shell_without_marker(cmd, handler=None, timeout=None):
        conn = _FakeShellConn("no marker in here", raw.shell_close_calls)
        handler(conn)

    raw.shell = shell_without_marker
    with pytest.raises(AdbError, match="exit marker"):
        Device(raw).sh("whatever")


def test_sh_transport_error_becomes_adberror():
    raw = FakeRaw()

    def boom(cmd, handler=None, timeout=None):
        raise RuntimeError("connection reset by peer")

    raw.shell = boom
    with pytest.raises(AdbError):
        Device(raw).sh("df")


def test_sh_closes_connection_even_when_read_fails():
    # Mirrors real ppadb's Transport.shell: the connection is handed to
    # `handler` before read_all() runs, so a read failure must not skip the
    # close (that's exactly the socket leak this fix addresses).
    raw = FakeRaw()
    raw.raise_on_read = TimeoutError("timed out")
    with pytest.raises(AdbError, match="failed"):
        Device(raw).sh("df")
    assert raw.shell_close_calls == [True]


# --- Device.push ---------------------------------------------------------------


def test_push_success_records_transfer(tmp_path):
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    raw = FakeRaw()
    before = _labeled(metrics.PUSH_BYTES_TOTAL, serial=raw.serial)
    Device(raw).push(str(f), "/sdcard/Download/a.tar")
    assert raw.pushed == [(str(f), "/sdcard/Download/a.tar")]
    assert _labeled(metrics.PUSH_BYTES_TOTAL, serial=raw.serial) == before + 4096


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


def test_push_raises_deadline_error_even_if_cancel_close_fails(monkeypatch, tmp_path):
    # A secondary error from closing the socket during cancellation must not
    # mask the deadline AdbError - that's the failure that actually happened
    # from the caller's point of view.
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    monkeypatch.setattr(C, "PUSH_TIMEOUT_FLOOR", 0.2)
    monkeypatch.setattr(C, "PUSH_MIN_BYTES_PER_SEC", 10**12)
    raw = FakeRaw(push_behavior=_push_stall_until_closed)
    raw.raise_on_close = OSError("bad file descriptor")

    with pytest.raises(AdbError, match="exceeded"):
        Device(raw).push(str(f), "/sdcard/Download/a.tar")


def test_push_logs_error_when_worker_ignores_close(monkeypatch, tmp_path, caplog):
    # Abnormal case: the worker doesn't unblock even after conn.close(). push()
    # must not claim it's confirmed dead - it should wait out the same
    # mechanism it relies on (stall_timeout) and then log the leak loudly.
    caplog.set_level(logging.ERROR)
    f = tmp_path / "a.tar"
    f.write_bytes(b"x" * 4096)
    monkeypatch.setattr(C, "PUSH_TIMEOUT_FLOOR", 0.05)
    monkeypatch.setattr(C, "PUSH_MIN_BYTES_PER_SEC", 10**12)
    monkeypatch.setattr(C, "PUSH_CANCEL_GRACE", 0.05)

    def _push_ignore_close(conn, src, dest):
        threading.Event().wait()  # blocks forever, ignoring conn.closed

    raw = FakeRaw(push_behavior=_push_ignore_close)

    with pytest.raises(AdbError, match="exceeded"):
        Device(raw).push(str(f), "/sdcard/Download/a.tar", stall_timeout=0.05)

    assert "did not exit after being cancelled" in caplog.text


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
    monkeypatch.setattr("uploadrr.adb.transport._CLIENT", client)
    with pytest.raises(AdbError, match="not connected"):
        get_device("test_serial")


def test_get_device_server_down(monkeypatch):
    client = MagicMock()
    client.device.side_effect = RuntimeError("Is adb running on your computer?")
    monkeypatch.setattr("uploadrr.adb.transport._CLIENT", client)
    with pytest.raises(AdbError, match="adb server unreachable"):
        get_device("test_serial")


def test_get_device_timeout_becomes_adberror(monkeypatch):
    client = MagicMock()
    client.device.side_effect = TimeoutError("timed out")
    monkeypatch.setattr("uploadrr.adb.transport._CLIENT", client)
    with pytest.raises(AdbError, match="adb server unreachable"):
        get_device("test_serial")


def test_get_device_success(monkeypatch):
    raw = FakeRaw()
    client = MagicMock()
    client.device.return_value = raw
    monkeypatch.setattr("uploadrr.adb.transport._CLIENT", client)
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

    monkeypatch.setattr(
        "uploadrr.adb.transport.AdbClient", lambda host, port: DummyClient()
    )
    monkeypatch.setattr("uploadrr.adb.transport._CLIENT", None)

    from uploadrr.adb.transport import _client

    client = _client()
    client.create_connection()
    client.create_connection(timeout=5)
    assert calls == [C.CONNECT_TIMEOUT, 5]


def test_connected_serials_filters_to_device_state(monkeypatch):
    """The state="device" filter is ppadb's own, applied before results ever
    reach us - assert it's passed through rather than reimplementing the
    filtering here (ppadb's Device wrapper drops the state token entirely)."""
    other = FakeRaw()
    other.serial = "other_serial"
    client = MagicMock()
    client.devices.return_value = [FakeRaw(), other]
    monkeypatch.setattr("uploadrr.adb.transport._CLIENT", client)

    result = connected_serials()

    client.devices.assert_called_once_with(state="device")
    assert result == {"test_serial", "other_serial"}


def test_connected_serials_empty_when_nothing_connected(monkeypatch):
    client = MagicMock()
    client.devices.return_value = []
    monkeypatch.setattr("uploadrr.adb.transport._CLIENT", client)

    assert connected_serials() == set()


def test_connected_serials_server_down_becomes_adberror(monkeypatch):
    client = MagicMock()
    client.devices.side_effect = RuntimeError("Is adb running on your computer?")
    monkeypatch.setattr("uploadrr.adb.transport._CLIENT", client)

    with pytest.raises(AdbError, match="adb server unreachable"):
        connected_serials()
