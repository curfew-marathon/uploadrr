import logging
import os
import re
import shlex
import threading
import time

from ppadb.client import Client as AdbClient

from uploadrr import constants as C

logger = logging.getLogger(__name__)

_CLIENT = None
_RC_MARKER = "__UPLOADRR_RC__="
_RC_RE = re.compile(re.escape(_RC_MARKER) + r"(\d+)\s*\Z")


class AdbError(OSError):
    """A device/transport failure. Subclass of OSError so callers that already
    handle OSError keep the source archive for a later retry."""


class AdbCommandError(AdbError):
    """An on-device command exited non-zero (ppadb itself never raises for this)."""

    def __init__(self, cmd, rc, output):
        super().__init__(f"`{cmd}` exited {rc}: {output.strip()[:500]}")
        self.cmd = cmd
        self.rc = rc
        self.output = output


class _PushAborted(Exception):
    """Raised from the push progress callback to unwind a doomed transfer."""


def _client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = AdbClient(host="127.0.0.1", port=5037)
    return _CLIENT


def get_device(serial):
    logger.debug("Connecting to device: %s", serial)
    try:
        raw = _client().device(serial)
    except RuntimeError as e:  # adb server not running / unreachable
        raise AdbError(f"adb server unreachable for {serial}: {e}") from e
    if raw is None:
        raise AdbError(f"Device {serial} not connected - check `adb devices`")
    return Device(raw)


class Device:
    """Thin wrapper over a ppadb device that adds per-call timeouts and turns
    non-zero shell exits and transport errors into exceptions."""

    def __init__(self, raw):
        self._raw = raw
        self.serial = raw.serial

    def sh(self, cmd, *, timeout=C.SHELL_TIMEOUT, check=True):
        """Run ``cmd`` on the device and return its combined output.

        Unlike ``ppadb``'s ``shell``, this raises ``AdbCommandError`` when the
        command exits non-zero (with ``check=True``) and ``AdbError`` on a
        transport error or timeout.
        """
        wrapped = f"{cmd}\necho {_RC_MARKER}$?"
        try:
            out = self._raw.shell(wrapped, timeout=timeout)
        except Exception as e:  # socket timeout, connection reset, ppadb RuntimeError
            raise AdbError(f"shell `{cmd}` failed on {self.serial}: {e}") from e

        out = out or ""
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

    def push(self, src, dest, *, stall_timeout=None):
        """Push a local file, raising ``AdbError`` if the transfer stalls or is
        too slow overall.

        ``ppadb``'s sync push has no timeout at all, so it runs on a daemon
        worker thread while this thread watches progress: it aborts if no bytes
        move for ``stall_timeout`` seconds, and the progress callback itself
        bails out if the whole transfer can't sustain a minimum average rate.
        Sizes here run from tens of MB to a couple of GB, so both guards are
        rate-based rather than a fixed wall-clock budget.
        """
        stall_timeout = stall_timeout or C.PUSH_STALL_TIMEOUT
        total = os.path.getsize(src)
        hard_deadline = time.monotonic() + max(
            C.PUSH_TIMEOUT_FLOOR, total / C.PUSH_MIN_BYTES_PER_SEC
        )
        state = {"tick": time.monotonic(), "sent": 0, "err": None, "done": False}

        def _progress(_name, _total, sent):
            state["sent"] = sent
            state["tick"] = time.monotonic()
            if time.monotonic() > hard_deadline:
                raise _PushAborted(
                    f"transfer below {C.PUSH_MIN_BYTES_PER_SEC} B/s "
                    f"(sent {sent}/{total} bytes)"
                )

        def _run():
            try:
                self._raw.push(src, dest, progress=_progress)
            except Exception as e:  # noqa: BLE001 - handed to the watching thread
                state["err"] = e
            finally:
                state["done"] = True

        worker = threading.Thread(
            target=_run, name=f"adb-push-{self.serial}", daemon=True
        )
        worker.start()

        poll = max(0.05, min(1.0, stall_timeout / 4))
        while not state["done"]:
            worker.join(timeout=poll)
            if state["done"]:
                break
            if time.monotonic() - state["tick"] > stall_timeout:
                raise AdbError(
                    f"push {src} -> {self.serial}:{dest} stalled for "
                    f"{stall_timeout}s at {state['sent']}/{total} bytes"
                )

        if state["err"] is not None:
            raise AdbError(
                f"push {src} -> {self.serial}:{dest} failed: {state['err']}"
            ) from state["err"]


def verify_free_space(device, file_size):
    logger.debug("Checking free space on device %s", device.serial)
    out = device.sh(f"df -k {shlex.quote(C.CAMERA)}")
    rows = [r for r in out.splitlines() if r.strip()]
    if len(rows) < 2:
        raise AdbError(f"Unexpected `df` output on {device.serial}: {out!r}")

    cols = rows[-1].split()
    try:
        free = int(cols[3]) * 1024  # "Available" column, 1K blocks
    except (IndexError, ValueError) as e:
        raise AdbError(f"Cannot parse `df` row on {device.serial}: {rows[-1]!r}") from e

    # Buffer for the tar file plus its extracted copy plus slack.
    required = file_size * 3
    logger.debug(
        "Device %s - free: %d bytes, required: %d bytes",
        device.serial,
        free,
        required,
    )
    if free < required:
        raise AdbError(
            f"Device {device.serial} - insufficient free space "
            f"(free: {free}, required: {required})"
        )
    logger.debug("Device %s - storage check passed", device.serial)


def _reject_unsafe_paths(entries):
    for e in entries:
        if e.startswith(("/", "../")) or "/../" in e:
            raise AdbError(f"Refusing archive with unsafe path entry: {e!r}")


def push_file(serial, file):
    logger.info("Starting transfer of %s to device %s", file, serial)
    device = get_device(serial)

    pre_work(device)
    file_size = os.stat(file).st_size
    logger.debug("File size: %d bytes", file_size)
    verify_free_space(device, file_size)

    file_dest = C.DOWNLOAD + os.path.basename(file)
    q_dest = shlex.quote(file_dest)
    q_camera = shlex.quote(C.CAMERA)

    scanned = []
    try:
        device.sh(f"mkdir -p {q_camera}")

        logger.info(
            "Pushing file to device %s: %s -> %s", device.serial, file, file_dest
        )
        device.push(file, file_dest)

        # Validate before extracting, and fail loudly instead of deleting the
        # source archive after a broken extraction.
        listing = device.sh(f"tar -tf {q_dest}", timeout=C.EXTRACT_TIMEOUT)
        entries = [e for e in listing.splitlines() if e.strip()]
        if not entries:
            raise AdbError(f"Archive {file_dest} on {device.serial} lists no entries")
        _reject_unsafe_paths(entries)

        logger.info("Extracting archive on device %s: %s", device.serial, file_dest)
        device.sh(f"tar -xf {q_dest} -C {q_camera}", timeout=C.EXTRACT_TIMEOUT)

        scanned = [C.CAMERA + e for e in entries if not e.endswith("/")]
        logger.info(
            "Extracted %d files to %s on device %s",
            len(scanned),
            C.CAMERA,
            device.serial,
        )
    finally:
        try:
            device.sh(f"rm -f {q_dest}", check=False)
        except AdbError as e:
            logger.warning(
                "Could not remove %s on device %s: %s", file_dest, device.serial, e
            )

    post_work(device, scanned)
    logger.info("Successfully completed transfer to device %s", device.serial)


def pre_work(device):
    # `am force-stop` was removed here: it aborts any in-progress Google Photos
    # upload job. If a stuck upload queue is ever observed it can come back as an
    # explicit, opt-in recovery step.
    logger.debug("pre_work: no-op for device %s", device.serial)


def post_work(device, scanned_paths):
    _ensure_interactive(device)
    # Leave Doze if a previous run (or a person) forced it.
    device.sh("dumpsys deviceidle unforce", check=False)
    _media_scan(device, scanned_paths)
    # UI nudge only; the media scan above is what enqueues the upload job.
    device.sh(
        f"monkey -p {C.PHOTOS_PKG} -c android.intent.category.LAUNCHER 1",
        check=False,
    )


def _interactive(device):
    """True when the screen is on and no keyguard is in the way."""
    power = device.sh(
        "dumpsys power | grep -E 'mWakefulness=|Display Power'", check=False
    )
    awake = "Awake" in power or "state=ON" in power

    keyguard = device.sh(
        "dumpsys window 2>/dev/null | "
        "grep -iE 'mShowingLockscreen|mDreamingLockscreen|KeyguardShowing'",
        check=False,
    ).lower()
    keyguard_up = "true" in keyguard

    return awake and not keyguard_up


def _swipe_up(device):
    size = device.sh("wm size", check=False)
    m = re.search(r"(\d+)x(\d+)", size)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (1080, 2400)
    device.sh(
        f"input touchscreen swipe {w // 2} {int(h * 0.8)} {w // 2} {int(h * 0.2)} 200",
        check=False,
    )


def _ensure_interactive(device, settle=1.5):
    """The target device has no secure lock, but its keyguard state varies:
    sometimes already interactive, sometimes screen-off, sometimes a non-secure
    keyguard is showing, sometimes it needs a swipe. Escalate until a `dumpsys`
    re-check confirms the device is interactive."""
    escalation = (
        lambda: device.sh("input keyevent KEYCODE_WAKEUP", check=False),
        lambda: device.sh("wm dismiss-keyguard", check=False),
        lambda: device.sh("input keyevent 82", check=False),  # MENU
        lambda: _swipe_up(device),
    )
    for step in escalation:
        if _interactive(device):
            break
        step()
        time.sleep(settle)
    else:
        if not _interactive(device):
            logger.warning(
                "Device %s not confirmed interactive after escalation - "
                "proceeding anyway",
                device.serial,
            )

    running = device.sh("dumpsys user | grep -i 'RunningUnlocked'", check=False).lower()
    if "false" in running:
        logger.warning(
            "Device %s has not been unlocked since boot (FBE) - Google Photos "
            "backup will not run until it is manually unlocked once.",
            device.serial,
        )


def _media_scan(device, paths, batch=40):
    if not paths:
        logger.debug("No extracted files to scan on device %s", device.serial)
        return
    for i in range(0, len(paths), batch):
        chunk = paths[i : i + batch]
        cmd = " ; ".join(
            "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
            f"-d {shlex.quote('file://' + p)}"
            for p in chunk
        )
        device.sh(cmd, check=False)
    logger.info(
        "Requested media scan of %d files on device %s", len(paths), device.serial
    )
