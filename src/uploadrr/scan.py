from __future__ import annotations

import logging
import os
from queue import Queue

from uploadrr import metrics
from uploadrr.config import Config

logger = logging.getLogger(__name__)

CONFIG = Config()


def count_pending_tars(path: str) -> int:
    """Count `.tar` files currently sitting in `path` (not subdirectories)."""
    try:
        entries = os.listdir(path)
    except OSError:
        # Can't read the directory right now; report 0 pending rather than
        # raising, since this only feeds a metrics gauge, not an authoritative count.
        return 0
    return sum(
        1
        for entry in entries
        if entry.endswith(".tar") and os.path.isfile(os.path.join(path, entry))
    )


def files_backfill(queue: Queue[str]) -> None:
    for d in CONFIG.get_data():
        add_files(d["archive"], queue)


def add_files(path: str, queue: Queue[str]) -> None:
    logger.debug("Scanning directory for existing tar files: %s", path)
    try:
        entries = os.listdir(path)
        tar_count = 0
        for file in entries:
            f = os.path.join(path, file)
            if os.path.isfile(f):
                if file.endswith(".tar"):
                    logger.info("Found existing tar file to process: %s", f)
                    queue.put(f)
                    tar_count += 1
                else:
                    logger.debug("Skipping non-tar file: %s", f)
            elif os.path.isdir(f):
                logger.debug("Skipping subdirectory: %s", f)
            else:
                logger.warning("Cannot resolve file type for: %s", f)

        if tar_count > 0:
            logger.info(
                "Added %d tar files from %s to processing queue", tar_count, path
            )
        else:
            logger.debug("No tar files found in %s", path)

        try:
            metrics.PENDING_TARS.labels(serial=CONFIG.get_serial(path)).set(tar_count)
        except KeyError:
            pass  # no device config for this directory; nothing to label it with

    except FileNotFoundError:
        logger.warning("Archive directory not found: %s", path)
    except PermissionError:
        logger.error("Permission denied accessing directory: %s", path)
    except Exception as e:  # noqa: BLE001
        # Deliberate catch-all beyond the two expected cases above: any other
        # os.listdir/os.path.isfile failure on this directory should not crash
        # the scan, it should just be logged and skipped.
        logger.error("Error scanning directory %s: %s", path, str(e))
