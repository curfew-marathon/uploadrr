from __future__ import annotations

import logging
import time
from collections import defaultdict
from queue import Queue
from typing import cast

from watchdog.events import FileSystemEvent, FileSystemEventHandler

logger = logging.getLogger(__name__)


class MonitorFolder(FileSystemEventHandler):
    def __init__(self, queue: Queue[str]) -> None:
        self.queue = queue
        # Track files being processed to avoid duplicates
        self.processed_files: set[str] = set()
        # Track last modification times to debounce rapid events
        self.last_event_time: defaultdict[str, float] = defaultdict(float)
        self.debounce_seconds = 2.0  # Wait 2 seconds after last event

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

        current_time = time.time()
        last_time = self.last_event_time[src_path]

        # Update the last event time
        self.last_event_time[src_path] = current_time

        # Debounce rapid events - only process if enough time has passed
        if current_time - last_time < self.debounce_seconds:
            logger.debug("Debouncing rapid events for: %s", src_path)
            return

        logger.debug("File closed: %s", src_path)
        logger.info("New tar file detected: %s", src_path)

        # Mark as processed and add to queue
        self.processed_files.add(src_path)
        self.queue.put(src_path)

        # Clean up tracking for this file
        if src_path in self.last_event_time:
            del self.last_event_time[src_path]
