from __future__ import annotations

import logging
import os
import time
from queue import Empty, Queue

from watchdog.observers import Observer

from uploadrr import adb, metrics, scan
from uploadrr.listener import MonitorFolder

logger = logging.getLogger(__name__)


def launch() -> None:
    logger.info("Starting uploadrr - setting up file observers")
    queue: Queue[str] = Queue()
    metrics.bind_queue_depth(queue)
    event_handler = MonitorFolder(queue)
    observer = Observer()
    observer.schedule(event_handler, path=scan.CONFIG.get_archive(), recursive=True)
    logger.info("Processing any existing tar files in archive directories")
    scan.files_backfill(queue)
    logger.info("File monitoring started - watching for new tar files")
    observer.start()

    # Track last periodic scan time
    last_scan_time = time.time()
    scan_interval = 86400  # 24 hours (86400 seconds)

    try:
        while True:
            try:
                # Wait for file with 24-hour timeout
                logger.debug("Waiting for file to process (timeout: 24 hours)...")
                f = queue.get(timeout=scan_interval)
                logger.info("Processing new file: %s", f)

                _dispatch_outcome(f, event_handler)

                logger.debug("Waiting 60 seconds before processing next file")
                time.sleep(60)  # Delay for 1 minute (60 seconds).

            except Empty:
                # Queue timeout - perform periodic scan (every 24 hours)
                current_time = time.time()
                hours_since_scan = (current_time - last_scan_time) / 3600
                logger.info(
                    "Queue timeout after 24 hours - performing periodic scan for unprocessed files"
                )
                logger.info("Last scan was %.1f hours ago", hours_since_scan)
                scan.files_backfill(queue)
                last_scan_time = current_time
                _record_device_connectivity()

    except KeyboardInterrupt:
        logger.info("Shutdown requested - stopping file observer")
        observer.stop()
    observer.join()
    logger.info("Uploadrr stopped")


def _dispatch_outcome(f: str, event_handler: MonitorFolder) -> None:
    with metrics.FILE_PROCESSING_SECONDS.time():
        try:
            process(f)
            metrics.FILES_PROCESSED_TOTAL.labels(outcome="success").inc()
            logger.info("Successfully processed: %s", f)
            # Clean up tracking for successfully processed files
            if (
                hasattr(event_handler, "processed_files")
                and f in event_handler.processed_files
            ):
                event_handler.processed_files.discard(f)
        except KeyError:
            metrics.FILES_PROCESSED_TOTAL.labels(outcome="no_device_config").inc()
            logger.warning("No device configuration found for file: %s", f)
            # Don't delete file - might be a temporary config issue
        except OSError as e:
            metrics.FILES_PROCESSED_TOTAL.labels(outcome="os_error").inc()
            logger.error(
                "Failed to process %s: %s - file will remain for retry",
                f,
                str(e),
            )
            # Don't delete file - could be temporary storage/device issue
        except Exception as e:  # noqa: BLE001
            metrics.FILES_PROCESSED_TOTAL.labels(outcome="unexpected_error").inc()
            logger.error("Unexpected error processing %s: %s", f, str(e))
            # Don't delete file for unexpected errors

        _update_pending_tars(f)


def _update_pending_tars(f: str) -> None:
    """Recount on-disk backlog for `f`'s directory, independent of the
    in-memory queue: a failed attempt leaves the queue immediately but leaves
    the tar on disk, so this is what actually catches "device is down" or
    "tars arriving faster than we push" - queue depth alone would not."""
    d = os.path.dirname(f)
    try:
        serial = scan.CONFIG.get_serial(d)
    except KeyError:
        return  # no device config for this directory; nothing to label it with
    metrics.PENDING_TARS.labels(serial=serial).set(scan.count_pending_tars(d))


def _record_device_connectivity() -> None:
    """Record which configured devices are currently reachable over adb.
    Runs off the existing 24h periodic-scan wake instead of a new poll loop."""
    try:
        connected = adb.connected_serials()
    except adb.AdbError as e:
        logger.warning("Could not enumerate connected devices: %s", e)
        return
    for d in scan.CONFIG.get_data():
        serial = d["serial"]
        metrics.DEVICE_CONNECTED.labels(serial=serial).set(
            1 if serial in connected else 0
        )


def process(f: str) -> None:
    d = os.path.dirname(f)
    logger.debug("Looking up device for directory: %s", d)
    serial = scan.CONFIG.get_serial(d)
    logger.debug("Found device %s for file %s", serial, f)
    adb.push_file(serial, f)
    logger.debug("Removing source file: %s", f)
    os.remove(f)
