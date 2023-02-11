import logging
import os
import time
from queue import Queue

from watchdog.observers import Observer

from uploadrr import adb
from uploadrr.config import Config
from uploadrr.listener import MonitorFolder

logger = logging.getLogger(__name__)

CONFIG = Config()


def launch():
    logger.info("Setting up the observers... ")
    queue = Queue()
    event_handler = MonitorFolder(queue)
    observer = Observer()
    observer.schedule(event_handler, path=CONFIG.get_archive(), recursive=True)
    logger.info("Adding any lingering files")
    files_backfill(queue)
    logger.info("Starting listener")
    observer.start()
    try:
        while (True):
            logger.debug("Waiting for file to process...")
            f = queue.get()
            logger.debug("Processing " + f)
            try:
                process(f)
                logger.debug("Completed " + f)
            except KeyError :
                logger.info("No device target for " + f)
            except IOError as e:
                logger.error(e)
            logger.debug("Give the device a moment to process")
            time.sleep(60)  # Delay for 1 minute (60 seconds).
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def process(f):
    d = os.path.dirname(f)
    serial = CONFIG.get_serial(d)
    adb.push_file(serial, f)
    os.remove(f)


def files_backfill(queue):
    for d in CONFIG.get_data():
        add_files(d.get('archive'), queue)


def add_files(path, queue):
    entries = os.listdir(path)
    for file in entries:
        f = os.path.join(path, file)
        if os.path.isfile(f):
            if file.endswith(".tar"):
                logger.info("Adding file " + f)
                queue.put(f)
            else:
                logger.info("Skipping file " + f)
        elif os.path.isdir(f):
            logger.info("Skipping directory " + f)
        else:
            logger.warning("Cannot resolve " + f)
