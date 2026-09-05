import logging

from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = logging.getLogger(__name__)

QUEUE_DEPTH = Gauge(
    "uploadrr_queue_depth", "Number of tar files currently queued for processing"
)

FILES_PROCESSED_TOTAL = Counter(
    "uploadrr_files_processed_total",
    "Tar files processed, by outcome",
    ["outcome"],  # "success" | "no_device_config" | "os_error" | "unexpected_error"
)

FILE_PROCESSING_SECONDS = Histogram(
    "uploadrr_file_processing_seconds",
    "Wall-clock time to process one tar file (push + extract + cleanup)",
)

# Buckets sized for multi-GB archive transfers (PUSH_TIMEOUT_FLOOR=300,
# EXTRACT_TIMEOUT=1800 in constants.py) - default prometheus_client buckets
# top out at 10s, useless here.
_PUSH_SECONDS_BUCKETS = (5, 15, 30, 60, 120, 300, 600, 1200, 1800, float("inf"))

PUSH_SECONDS = Histogram(
    "uploadrr_push_seconds",
    "Wall-clock time to push+extract one archive on a device",
    ["serial"],
    buckets=_PUSH_SECONDS_BUCKETS,
)
PUSH_BYTES_TOTAL = Counter(
    "uploadrr_push_bytes_total", "Bytes successfully pushed to a device", ["serial"]
)
PUSH_FAILURES_TOTAL = Counter(
    "uploadrr_push_failures_total", "Failed transfer attempts, by device", ["serial"]
)


def bind_queue_depth(q):
    """Wire the queue-depth gauge to `q.qsize()`. Called once from files.launch()."""
    QUEUE_DEPTH.set_function(q.qsize)


def start(port):
    """Start the metrics HTTP server. Never raises: a port collision or other
    startup failure is logged and swallowed so a scrape-endpoint problem can
    never take the daemon down."""
    try:
        start_http_server(port)
        logger.info("Metrics server listening on :%d/metrics", port)
    except OSError as e:
        logger.error(
            "Could not start metrics server on port %d (%s) - continuing without metrics",
            port,
            e,
        )
