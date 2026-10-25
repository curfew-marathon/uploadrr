from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
from typing import Any

from ppadb.client import Client as AdbClient
from ppadb.sync import Sync

from uploadrr import constants as C
from uploadrr import metrics

logger = logging.getLogger(__name__)

_CLIENT: AdbClient | None = None
_RC_MARKER = "__UPLOADRR_RC__="
_RC_RE = re.compile(re.escape(_RC_MARKER) + r"(\d+)\s*\Z")


class AdbError(OSError):
    """A device/transport failure. Subclass of OSError so callers that already
    handle OSError keep the source archive for a later retry."""


class AdbCommandError(AdbError):
    """An on-device command exited non-zero (ppadb itself never raises for this)."""

    def __init__(self, cmd: str, rc: int, output: str) -> None:
        super().__init__(f"`{cmd}` exited {rc}: {output.strip()[:500]}")
        self.cmd = cmd
        self.rc = rc
        self.output = output


def _client() -> AdbClient:
    """Return the module-wide adb-server client, creating it on first use.

    ppadb's host commands (``devices()``, ``device(serial)``) call
    ``create_connection()`` with no timeout, so the instance's method is
    wrapped with a default: without it, a stalled adb server could block
    `get_device` before any per-call timeout ever applies.
    """
    global _CLIENT
    if _CLIENT is None:
        client = AdbClient(host="127.0.0.1", port=5037)
        bound_create_connection = client.create_connection
        client.create_connection = lambda timeout=None: bound_create_connection(
            timeout=timeout or C.CONNECT_TIMEOUT
        )
        _CLIENT = client
    return _CLIENT


def get_device(serial: str) -> Device:
    """Look up a connected device by serial, wrapped as a `Device`."""
    logger.debug("Connecting to device: %s", serial)
    try:
        raw = _client().device(serial)
    except Exception as e:  # socket timeout, adb server down, transport error
        raise AdbError(f"adb server unreachable for {serial}: {e}") from e
    if raw is None:
        raise AdbError(f"Device {serial} not connected - check `adb devices`")
    return Device(raw)


def connected_serials() -> set[str]:
    """Return the set of serials adb currently reports as connected and
    authorized (`adb devices` state "device") to the host adb server."""
    try:
        devices = _client().devices(state="device")
    except Exception as e:  # socket timeout, adb server down, transport error
        raise AdbError(f"adb server unreachable while listing devices: {e}") from e
    return {d.serial for d in devices}


class Device:
    """Thin wrapper over a ppadb device that adds per-call timeouts and turns
    non-zero shell exits and transport errors into exceptions."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self.serial: str = raw.serial

    def sh(
        self, cmd: str, *, timeout: float = C.SHELL_TIMEOUT, check: bool = True
    ) -> str:
        """Run ``cmd`` on the device and return its combined output.

        Unlike ``ppadb``'s ``shell``, this raises ``AdbCommandError`` when the
        command exits non-zero (with ``check=True``) and ``AdbError`` on a
        transport error or timeout.

        ppadb's own ``shell()`` only closes its connection *after*
        ``read_all()`` returns, so a timeout there would otherwise leak the
        socket. Passing a ``handler`` hands the raw connection back instead of
        letting ppadb read/close it, so the read can be owned here under a
        ``try/finally`` that always closes it.
        """
        wrapped = f"{cmd}\necho {_RC_MARKER}$?"
        raw_out: str | None = None

        def _read_and_close(conn: Any) -> None:
            nonlocal raw_out
            try:
                raw_out = conn.read_all().decode("utf-8")
            finally:
                with contextlib.suppress(Exception):
                    conn.close()

        try:
            self._raw.shell(wrapped, handler=_read_and_close, timeout=timeout)
        except Exception as e:  # socket timeout, connection reset, ppadb RuntimeError
            raise AdbError(f"shell `{cmd}` failed on {self.serial}: {e}") from e

        out = raw_out or ""
        m = _RC_RE.search(out)
        if m is None:
            raise AdbError(
                f"shell `{cmd}` on {self.serial}: missing exit marker in output"
            )
        rc = int(m.group(1))
        body = out[: m.start()]
        if check and rc != 0:
            raise AdbCommandError(cmd, rc, body)
        return body

    def push(self, src: str, dest: str, *, stall_timeout: float | None = None) -> None:
        """Push a local file over a Sync connection this call owns.

        ``ppadb``'s own ``push()`` has no timeout: a stalled transfer used to
        leave an abandoned worker thread that could keep writing ``dest``
        after the caller cleaned it up. Owning the connection lets a stall be
        torn down for real: the socket gets a timeout that bounds every
        send/recv in the data phase, and if the worker is still alive after
        the overall deadline its socket is closed and it is joined for as
        long as that same timeout could take to unblock it, so by the time
        this call returns the worker is confirmed dead - or, in the abnormal
        case that it still isn't, that leak is logged loudly instead of
        silently claimed away.
        """
        stall_timeout = stall_timeout or C.PUSH_STALL_TIMEOUT
        total = os.path.getsize(src)
        overall = max(C.PUSH_TIMEOUT_FLOOR, total / C.PUSH_MIN_BYTES_PER_SEC)

        conn: Any = None
        try:
            conn = self._raw.sync()
            conn.socket.settimeout(stall_timeout)
        except Exception as e:
            if conn is not None:
                # Best-effort teardown: ppadb's Sync connection doesn't always
                # expose a clean close() when the socket failed mid-setup, and
                # a secondary error here must not mask the real one below.
                with contextlib.suppress(Exception):
                    conn.close()
            raise AdbError(
                f"push {src} -> {self.serial}:{dest} could not start: {e}"
            ) from e

        result: dict[str, Exception | None] = {"err": None}

        def _run() -> None:
            try:
                with conn:
                    Sync(conn).push(src, dest, Sync.DEFAULT_CHMOD, progress=None)
            except Exception as e:  # noqa: BLE001 - reported to the waiting thread
                result["err"] = e

        worker = threading.Thread(
            target=_run, name=f"adb-push-{self.serial}", daemon=True
        )
        worker.start()
        worker.join(overall)

        if worker.is_alive():
            # tells a blocked send()/recv() to unblock via the socket timeout;
            # a secondary error here must not mask the deadline AdbError below.
            with contextlib.suppress(Exception):
                conn.close()
            # close() isn't a guaranteed synchronous cancellation, but the
            # socket timeout we set up front is: give it that long (plus a
            # margin) to actually take effect before deciding the worker is
            # stuck for real.
            worker.join(stall_timeout + C.PUSH_CANCEL_GRACE)
            if worker.is_alive():
                logger.error(
                    "Device %s: push worker for %s did not exit after being "
                    "cancelled; abandoning it (a stale adb connection may "
                    "leak until this process exits)",
                    self.serial,
                    dest,
                )
            # Closing the socket may also land a secondary error in
            # result["err"], but the deadline is what actually happened from
            # the caller's point of view, so it takes precedence.
            raise AdbError(
                f"push {src} -> {self.serial}:{dest} exceeded {overall:.0f}s"
            )

        if result["err"] is not None:
            raise AdbError(
                f"push {src} -> {self.serial}:{dest} failed: {result['err']}"
            ) from result["err"]

        metrics.PUSH_BYTES_TOTAL.labels(serial=self.serial).inc(total)
