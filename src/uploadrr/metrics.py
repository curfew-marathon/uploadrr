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

# Buckets sized from Device.push()'s own deadline math: total_bytes /
# PUSH_MIN_BYTES_PER_SEC (262144 B/s in constants.py). A 2 GiB archive at that
# throughput floor legitimately takes ~8192s (~137 min) to push while still
# succeeding, so buckets must extend well past a 1800s ceiling or valid slow
# transfers collapse into +Inf.
_TRANSFER_SECONDS_BUCKETS = (
    5,
    15,
    30,
    60,
    120,
    300,
    600,
    1200,
    1800,
    3600,
    7200,
    14400,
    float("inf"),
)

FILE_PROCESSING_SECONDS = Histogram(
    "uploadrr_file_processing_seconds",
    "Wall-clock time to process one tar file (push + extract + cleanup)",
    buckets=_TRANSFER_SECONDS_BUCKETS,
)

PUSH_SECONDS = Histogram(
    "uploadrr_push_seconds",
    "Wall-clock time to push+extract one archive on a device",
    ["serial"],
    buckets=_TRANSFER_SECONDS_BUCKETS,
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
    """Start the metrics HTTP server. Never raises: an invalid port, a
    collision, or any other startup failure is logged and swallowed so a
    metrics problem can never take the daemon down."""
    try:
        start_http_server(int(port))
        logger.info("Metrics server listening on :%s/metrics", port)
    except (ValueError, OverflowError, OSError) as e:
        logger.error(
            "Could not start metrics server on port %r (%s) - continuing without metrics",
            port,
            e,
        )
