import logging
import os

from uploadrr import files

logger = logging.getLogger(__name__)

# Set default logging level from environment variable, defaulting to INFO
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO),
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

# Reduce watchdog's internal logging verbosity
logging.getLogger('watchdog').setLevel(logging.WARNING)
logging.getLogger('watchdog.observers').setLevel(logging.WARNING)
logging.getLogger('watchdog.observers.inotify_buffer').setLevel(logging.WARNING)

if __name__ == '__main__':
    files.launch()
