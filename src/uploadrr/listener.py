import logging
import time
from collections import defaultdict

from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)


class MonitorFolder(FileSystemEventHandler):

    def __init__(self, queue):
        self.queue = queue
        # Track files being processed to avoid duplicates
        self.processed_files = set()
        # Track last modification times to debounce rapid events
        self.last_event_time = defaultdict(float)
        self.debounce_seconds = 2.0  # Wait 2 seconds after last event

    def on_closed(self, event):
        if not event.src_path.endswith(".tar"):
            return
            
        # Skip if we've already processed this file
        if event.src_path in self.processed_files:
            logger.debug("Skipping already processed file: %s", event.src_path)
            return
            
        current_time = time.time()
        last_time = self.last_event_time[event.src_path]
        
        # Update the last event time
        self.last_event_time[event.src_path] = current_time
        
        # Debounce rapid events - only process if enough time has passed
        if current_time - last_time < self.debounce_seconds:
            logger.debug("Debouncing rapid events for: %s", event.src_path)
            return
            
        logger.debug("File closed: %s", event.src_path)
        logger.info("New tar file detected: %s", event.src_path)
        
        # Mark as processed and add to queue
        self.processed_files.add(event.src_path)
        self.queue.put(event.src_path)
        
        # Clean up tracking for this file
        if event.src_path in self.last_event_time:
            del self.last_event_time[event.src_path]
