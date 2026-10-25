from __future__ import annotations

import logging
import os
import shlex
from collections.abc import Callable

from uploadrr import constants as C
from uploadrr import metrics
from uploadrr.adb import archive, transport, ui
from uploadrr.adb.transport import AdbError, Device

logger = logging.getLogger(__name__)


def _free_bytes(device: Device) -> int:
    """Return free space (bytes) on the device's camera storage, via `df -k`."""
    out = device.sh(f"df -k {shlex.quote(C.CAMERA)}")
    rows = [r for r in out.splitlines() if r.strip()]
    if len(rows) < 2:
        raise AdbError(f"Unexpected `df` output on {device.serial}: {out!r}")

    cols = rows[-1].split()
    try:
        return int(cols[3]) * 1024  # "Available" column, 1K blocks
    except (IndexError, ValueError) as e:
        raise AdbError(f"Cannot parse `df` row on {device.serial}: {rows[-1]!r}") from e


def _best_effort(fn: Callable[[], object], warn_msg: str, *warn_args: object) -> None:
    """Run `fn`, converting AdbError into a logged warning instead of letting
    it propagate - for cleanup/informational steps that must never mask the
    real outcome of a push that already succeeded or already failed for
    another reason. `warn_args` are passed as separate lazy logging args
    (not interpolated into `warn_msg` by the caller), so a `%` in e.g. an
    archive filename or device serial can't be misread as a format
    directive. `warn_msg`'s final placeholder is always the caught error."""
    try:
        fn()
    except AdbError as e:
        logger.warning(warn_msg, *warn_args, e)


def verify_free_space(device: Device, file_size: int) -> None:
    """Raise `AdbError` unless the device has roughly 3x `file_size` free."""
    logger.debug("Checking free space on device %s", device.serial)
    free = _free_bytes(device)
    metrics.DEVICE_FREE_BYTES.labels(serial=device.serial).set(free)

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


def push_file(serial: str, file: str) -> None:
    """Push, validate, and extract one archive on `serial`, then post-process."""
    logger.info("Starting transfer of %s to device %s", file, serial)
    # validated locally before anything is pushed
    names = archive._archive_members(file)

    try:
        device = transport.get_device(serial)

        pre_work(device)
        file_size = os.stat(file).st_size
        logger.debug("File size: %d bytes", file_size)

        file_dest = C.DOWNLOAD + os.path.basename(file)
        q_dest = shlex.quote(file_dest)
        q_camera = shlex.quote(C.CAMERA)

        # Create the extraction target before checking free space on it: on a
        # fresh device /sdcard/DCIM may not exist yet, and `df` on a missing
        # path fails, which would otherwise block every transfer forever.
        device.sh(f"mkdir -p {q_camera}")
        verify_free_space(device, file_size)

        with metrics.PUSH_SECONDS.labels(serial=serial).time():
            try:
                logger.info(
                    "Pushing file to device %s: %s -> %s",
                    device.serial,
                    file,
                    file_dest,
                )
                device.push(file, file_dest)

                logger.info(
                    "Extracting archive on device %s: %s", device.serial, file_dest
                )
                device.sh(f"tar -xf {q_dest} -C {q_camera}", timeout=C.EXTRACT_TIMEOUT)

                scanned = [C.CAMERA + n for n in names]
                logger.info(
                    "Extracted %d files to %s on device %s",
                    len(scanned),
                    C.CAMERA,
                    device.serial,
                )
            finally:
                _best_effort(
                    lambda: device.sh(f"rm -f {q_dest}", check=True),
                    "Could not remove %s on device %s: %s",
                    file_dest,
                    device.serial,
                )

                # Informational only: the transfer already succeeded, so a
                # failure here must never mask that outcome. This is the
                # number that matters for trending real device fullness - the
                # pre-push read above is a conservative gate, not the
                # post-transfer truth.
                _best_effort(
                    lambda: metrics.DEVICE_FREE_BYTES.labels(serial=device.serial).set(
                        _free_bytes(device)
                    ),
                    "Could not read free space on device %s after cleanup: %s",
                    device.serial,
                )

            post_work(device, scanned)
    except AdbError:
        metrics.PUSH_FAILURES_TOTAL.labels(serial=serial).inc()
        raise

    logger.info("Successfully completed transfer to device %s", serial)


def pre_work(device: Device) -> None:
    """Placeholder for pre-transfer device prep; currently a deliberate no-op.

    `am force-stop` was removed here: it aborts any in-progress Google Photos
    upload job. If a stuck upload queue is ever observed it can come back as
    an explicit, opt-in recovery step.
    """
    logger.debug("pre_work: no-op for device %s", device.serial)


def post_work(device: Device, scanned_paths: list[str]) -> None:
    """Wake/unlock the device, then trigger a media scan of the new files."""
    ui._ensure_interactive(device)
    ui._warn_if_never_unlocked(device)
    # Leave Doze if a previous run (or a person) forced it.
    device.sh("dumpsys deviceidle unforce", check=False)
    ui._media_scan(device, scanned_paths)
    # UI nudge only; the media scan above is what enqueues the upload job.
    device.sh(
        f"monkey -p {C.PHOTOS_PKG} -c android.intent.category.LAUNCHER 1",
        check=False,
    )
