import io
import tarfile
import threading
from unittest.mock import MagicMock

import pytest

from uploadrr.adb.transport import _RC_MARKER

_SPLIT = "\necho " + _RC_MARKER

DF_OK = (
    "Filesystem     1K-blocks    Used  Available Use% Mounted on\n"
    "/dev/fuse      100000000    1000   99999000   1% /storage/emulated"
)
DF_LOW = (
    "Filesystem 1K-blocks Used Available Use% Mounted on\n"
    "/dev/fuse        512  384       128  75% /storage/emulated"
)
DF_TOO_FEW_COLUMNS = "Filesystem 1K-blocks\n/dev/fuse 100000000"
DF_NON_NUMERIC_AVAILABLE = (
    "Filesystem 1K-blocks Used Available Use% Mounted on\n"
    "/dev/fuse  100000000 1000        N/A  1% /storage/emulated"
)
# Header has an "Available" column, but the data row is short a field (e.g.
# a wrapped mount-source line) - the "Available" column exists but this row
# doesn't reach it.
DF_ROW_SHORTER_THAN_AVAILABLE_COLUMN = (
    "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/fuse 100000000"
)
# "Available" is NOT at the fixed index (3) every other fixture happens to
# use - this is what actually exercises the header-based lookup itself,
# rather than coincidentally matching a hardcoded column position.
DF_AVAILABLE_AT_NONSTANDARD_INDEX = (
    "Filesystem 1K-blocks Used Use% Available Mounted on\n"
    "/dev/fuse 100000000 1000 1% 99999000 /storage/emulated"
)


def _labeled(counter, **labels):
    return counter.labels(**labels)._value.get()


def _histogram_count(histogram, **labels):
    child = histogram.labels(**labels) if labels else histogram
    return next(s.value for s in child._child_samples() if s.name == "_count")


def _push_success(conn, src, dest):
    pass


def _push_stall_until_closed(conn, src, dest):
    while not conn.closed.wait(0.02):
        pass
    raise OSError("Bad file descriptor")


def _push_boom(conn, src, dest):
    raise RuntimeError("device offline")


class _FakeSyncConn:
    """Stands in for the ppadb Sync connection that `Device.push` owns."""

    def __init__(self, behavior, recorder, raise_on_close=None):
        self.socket = MagicMock()
        self.behavior = behavior
        self.recorder = recorder
        self.closed = threading.Event()
        self._raise_on_close = raise_on_close

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def close(self):
        self.closed.set()
        if self._raise_on_close is not None:
            raise self._raise_on_close


class _FakeSync:
    """Stands in for `ppadb.sync.Sync`, driven entirely through FakeRaw.sync()."""

    DEFAULT_CHMOD = 0o644

    def __init__(self, conn):
        self.conn = conn

    def push(self, src, dest, mode, progress=None):
        self.conn.recorder.append((src, dest))
        self.conn.behavior(self.conn, src, dest)


@pytest.fixture
def _patch_sync(monkeypatch):
    """Every Device.push test drives ppadb's Sync through `_FakeSync`. Patching
    the name inside uploadrr.adb.transport (rather than shadowing `ppadb` itself
    in sys.modules) keeps the real ppadb package importable everywhere else in
    the suite, regardless of which test file/order runs first."""
    monkeypatch.setattr("uploadrr.adb.transport.Sync", _FakeSync)


class _FakeShellConn:
    """Stands in for the ppadb connection handed to Device.sh's `handler`."""

    def __init__(self, output, close_calls, raise_on_read=None):
        self._output = output.encode("utf-8")
        self._close_calls = close_calls
        self._raise_on_read = raise_on_read

    def read_all(self):
        if self._raise_on_read is not None:
            raise self._raise_on_read
        return self._output

    def close(self):
        self._close_calls.append(True)


class FakeRaw:
    """Stand-in for a ppadb device: records shell calls, replays canned output."""

    def __init__(self, responses=None, push_behavior=_push_success):
        self.serial = "test_serial"
        self.shell_calls = []
        self.shell_close_calls = []
        self.pushed = []
        self.sync_conns = []
        self._responses = responses or {}
        self.push_behavior = push_behavior
        # Test hook: an exception instance for the next shell()'s read_all()
        # to raise, to exercise Device.sh's close-on-failure path (mirrors
        # real ppadb's Transport.shell, which hands the connection to
        # `handler` and lets read_all() raise before anything is closed).
        self.raise_on_read = None
        # Test hook: an exception instance for the next sync connection's
        # close() to raise, to exercise Device.push's cancellation path when
        # tearing down the socket itself fails.
        self.raise_on_close = None

    def shell(self, cmd, handler=None, timeout=None):
        real = cmd.rsplit(_SPLIT, 1)[0]
        self.shell_calls.append(real)
        body, rc = "", 0
        for needle, value in self._responses.items():
            if needle in real:
                body, rc = value if isinstance(value, tuple) else (value, 0)
                break
        sep = "\n" if body else ""
        output = f"{body}{sep}{_RC_MARKER}{rc}\n"
        conn = _FakeShellConn(output, self.shell_close_calls, self.raise_on_read)

        if handler is not None:
            handler(conn)
            return None
        result = conn.read_all()
        conn.close()
        return result.decode("utf-8")

    def sync(self):
        conn = _FakeSyncConn(self.push_behavior, self.pushed, self.raise_on_close)
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
