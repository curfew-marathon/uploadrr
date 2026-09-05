import contextlib
import logging
import os
import re
import shlex
import tarfile
import threading
import time
from urllib.parse import quote

from ppadb.client import Client as AdbClient
from ppadb.sync import Sync

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


def _client():
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


def get_device(serial):
    """Look up a connected device by serial, wrapped as a `Device`."""
    logger.debug("Connecting to device: %s", serial)
    try:
        raw = _client().device(serial)
    except Exception as e:  # socket timeout, adb server down, transport error
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

        ppadb's own ``shell()`` only closes its connection *after*
        ``read_all()`` returns, so a timeout there would otherwise leak the
        socket. Passing a ``handler`` hands the raw connection back instead of
        letting ppadb read/close it, so the read can be owned here under a
        ``try/finally`` that always closes it.
        """
        wrapped = f"{cmd}\necho {_RC_MARKER}$?"
        raw_out = None

        def _read_and_close(conn):
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

    def push(self, src, dest, *, stall_timeout=None):
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

        conn = None
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

        result = {"err": None}

        def _run():
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
            conn.close()  # tells a blocked send()/recv() to unblock via the socket timeout
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


def verify_free_space(device, file_size):
    """Raise `AdbError` unless the device has roughly 3x `file_size` free."""
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


def _archive_members(path):
    """Validate a local tar archive and return its regular-file member names.

    Read locally rather than via the device's `tar -tf`, so link targets and
    path traversal can be checked directly instead of trusting names alone:
    a symlink member can point outside the extraction directory and pass a
    name-only check, then have later members extract through it.
    """
    names = []
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            parts = member.name.split("/")
            if member.name.startswith("/") or ".." in parts:
                raise AdbError(f"Refusing archive: unsafe path {member.name!r}")
            if not (member.isfile() or member.isdir()):
                raise AdbError(
                    f"Refusing archive: {member.name!r} is a link or special file"
                )
            if member.isfile():
                names.append(member.name)
    if not names:
        raise AdbError(f"Archive {path} contains no regular files")
    return names


def push_file(serial, file):
    """Push, validate, and extract one archive on `serial`, then post-process."""
    logger.info("Starting transfer of %s to device %s", file, serial)
    names = _archive_members(file)  # validated locally before anything is pushed
    device = get_device(serial)

    pre_work(device)
    file_size = os.stat(file).st_size
    logger.debug("File size: %d bytes", file_size)

    file_dest = C.DOWNLOAD + os.path.basename(file)
    q_dest = shlex.quote(file_dest)
    q_camera = shlex.quote(C.CAMERA)

    # Create the extraction target before checking free space on it: on a
    # fresh device /sdcard/DCIM may not exist yet, and `df` on a missing path
    # fails, which would otherwise block every transfer forever.
    device.sh(f"mkdir -p {q_camera}")
    verify_free_space(device, file_size)

    try:
        logger.info(
            "Pushing file to device %s: %s -> %s", device.serial, file, file_dest
        )
        device.push(file, file_dest)

        logger.info("Extracting archive on device %s: %s", device.serial, file_dest)
        device.sh(f"tar -xf {q_dest} -C {q_camera}", timeout=C.EXTRACT_TIMEOUT)

        scanned = [C.CAMERA + n for n in names]
        logger.info(
            "Extracted %d files to %s on device %s",
            len(scanned),
            C.CAMERA,
            device.serial,
        )
    finally:
        try:
            device.sh(f"rm -f {q_dest}", check=True)
        except AdbError as e:
            logger.warning(
                "Could not remove %s on device %s: %s", file_dest, device.serial, e
            )

    post_work(device, scanned)
    logger.info("Successfully completed transfer to device %s", device.serial)


def pre_work(device):
    """Placeholder for pre-transfer device prep; currently a deliberate no-op.

    `am force-stop` was removed here: it aborts any in-progress Google Photos
    upload job. If a stuck upload queue is ever observed it can come back as
    an explicit, opt-in recovery step.
    """
    logger.debug("pre_work: no-op for device %s", device.serial)


def post_work(device, scanned_paths):
    """Wake/unlock the device, then trigger a media scan of the new files."""
    _ensure_interactive(device)
    # Leave Doze if a previous run (or a person) forced it.
    device.sh("dumpsys deviceidle unforce", check=False)
    _media_scan(device, scanned_paths)
    # UI nudge only; the media scan above is what enqueues the upload job.
    device.sh(
        f"monkey -p {C.PHOTOS_PKG} -c android.intent.category.LAUNCHER 1",
        check=False,
    )


def _screen_awake(device):
    """True when `dumpsys power` reports the display is on."""
    power = device.sh(
        "dumpsys power | grep -E 'mWakefulness=|Display Power'", check=False
    )
    return "Awake" in power or "state=ON" in power


def _keyguard_clear(device):
    """True only on an affirmative "no keyguard" signal from `dumpsys window`.

    Unrecognized or empty output (a failed pipe, a renamed field on a newer
    Android version) is treated as still locked, not as clear - otherwise an
    awake-but-locked device would look interactive and skip the escalation
    entirely.
    """
    keyguard = device.sh(
        "dumpsys window 2>/dev/null | "
        "grep -iE 'mShowingLockscreen|mDreamingLockscreen|KeyguardShowing'",
        check=False,
    ).lower()
    if "true" in keyguard:
        return False
    return "false" in keyguard  # explicit "not showing"; unrecognized output -> False


def _interactive(device):
    """True when the screen is confirmed awake and the keyguard confirmed down."""
    return _screen_awake(device) and _keyguard_clear(device)


def _swipe_up(device):
    """Swipe up from near the bottom of the screen to dismiss a keyguard."""
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
            # Still worth proceeding (wake + swipe are harmless), but only
            # worth a warning if the screen itself never came on.
            log = logger.info if _screen_awake(device) else logger.warning
            log(
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
    """Broadcast MEDIA_SCANNER_SCAN_FILE for each extracted path in batches."""
    if not paths:
        logger.debug("No extracted files to scan on device %s", device.serial)
        return
    for i in range(0, len(paths), batch):
        chunk = paths[i : i + batch]
        # `&&`, not `;`: a broadcast that fails to dispatch at all is a real
        # sign the scan mechanism is broken on this device, so it should
        # propagate rather than be hidden behind the last command's status.
        # (Note `am broadcast` still exits 0 even when a receiver ignores the
        # intent - this only catches failures to dispatch in the first place.)
        cmd = " && ".join(
            "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
            f"-d {shlex.quote('file://' + quote(p, safe='/'))}"
            for p in chunk
        )
        device.sh(cmd)
    logger.info(
        "Requested media scan of %d files on device %s", len(paths), device.serial
    )
