import logging

from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)


class MonitorFolder(FileSystemEventHandler):

    def __init__(self, queue):
        self.queue = queue

    def on_closed(self, event):
        logger.debug("on_closed " + event.src_path)
        if event.src_path.endswith(".tar"):
            self.queue.put(event.src_path)
