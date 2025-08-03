import logging
import os
import time
from queue import Queue, Empty

from watchdog.observers import Observer

from uploadrr import adb
from uploadrr.config import Config
from uploadrr.listener import MonitorFolder

logger = logging.getLogger(__name__)

CONFIG = Config()


def launch():
    logger.info("Starting uploadrr - setting up file observers")
    queue = Queue()
    event_handler = MonitorFolder(queue)
    observer = Observer()
    observer.schedule(event_handler, path=CONFIG.get_archive(), recursive=True)
    logger.info("Processing any existing tar files in archive directories")
    files_backfill(queue)
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
                
                try:
                    process(f)
                    logger.info("Successfully processed: %s", f)
                    # Clean up tracking for successfully processed files
                    if hasattr(event_handler, 'processed_files') and f in event_handler.processed_files:
                        event_handler.processed_files.discard(f)
                except KeyError:
                    logger.warning("No device configuration found for file: %s", f)
                    # Don't delete file - might be a temporary config issue
                except IOError as e:
                    logger.error("Failed to process %s: %s - file will remain for retry", f, str(e))
                    # Don't delete file - could be temporary storage/device issue
                except Exception as e:
                    logger.error("Unexpected error processing %s: %s", f, str(e))
                    # Don't delete file for unexpected errors
                
                logger.debug("Waiting 60 seconds before processing next file")
                time.sleep(60)  # Delay for 1 minute (60 seconds).
                
            except Empty:
                # Queue timeout - perform periodic scan (every 24 hours)
                current_time = time.time()
                hours_since_scan = (current_time - last_scan_time) / 3600
                logger.info("Queue timeout after 24 hours - performing periodic scan for unprocessed files")
                logger.info("Last scan was %.1f hours ago", hours_since_scan)
                files_backfill(queue)
                last_scan_time = current_time
                    
    except KeyboardInterrupt:
        logger.info("Shutdown requested - stopping file observer")
        observer.stop()
    observer.join()
    logger.info("Uploadrr stopped")


def process(f):
    d = os.path.dirname(f)
    logger.debug("Looking up device for directory: %s", d)
    serial = CONFIG.get_serial(d)
    logger.debug("Found device %s for file %s", serial, f)
    adb.push_file(serial, f)
    logger.debug("Removing source file: %s", f)
    os.remove(f)


def files_backfill(queue):
    for d in CONFIG.get_data():
        add_files(d.get('archive'), queue)


def add_files(path, queue):
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
            logger.info("Added %d tar files from %s to processing queue", tar_count, path)
        else:
            logger.debug("No tar files found in %s", path)
            
    except FileNotFoundError:
        logger.warning("Archive directory not found: %s", path)
    except PermissionError:
        logger.error("Permission denied accessing directory: %s", path)
    except Exception as e:
        logger.error("Error scanning directory %s: %s", path, str(e))
