from __future__ import annotations

import logging
from queue import Queue
from typing import cast

from watchdog.events import FileSystemEvent, FileSystemEventHandler

logger = logging.getLogger(__name__)


class MonitorFolder(FileSystemEventHandler):
    def __init__(self, queue: Queue[str]) -> None:
        self.queue = queue
        # Track files being processed to avoid duplicates
        self.processed_files: set[str] = set()

    def on_closed(self, event: FileSystemEvent) -> None:
        # watchdog types src_path as `str | bytes` to cover bytes-mode watches,
        # but this codebase never schedules one (Observer.schedule() is always
        # called with a str path), so src_path is always str in practice.
        src_path = cast(str, event.src_path)

        if not src_path.endswith(".tar"):
            return

        # Skip if we've already processed this file
        if src_path in self.processed_files:
            logger.debug("Skipping already processed file: %s", src_path)
            return

        logger.debug("File closed: %s", src_path)
        logger.info("New tar file detected: %s", src_path)

        # Mark as processed and add to queue
        self.processed_files.add(src_path)
        self.queue.put(src_path)
