STORAGE = "/storage"
DOWNLOAD = "/sdcard/Download/"
CAMERA = "/sdcard/DCIM/"
CANDIDATES = ["config.ini", "/config/config.ini"]

PHOTOS_PKG = "com.google.android.apps.photos"

# ppadb socket timeouts, in seconds.
CONNECT_TIMEOUT = 10  # adb-server discovery: `devices()` / `device(serial)`
SHELL_TIMEOUT = 30  # df, input, am, dumpsys and other short control commands
EXTRACT_TIMEOUT = 1800  # on-device `tar -xf` of a multi-GB archive

# Archives run from tens of MB to a couple of GB, so the push guard is based on
# throughput, not a fixed wall-clock budget: abort if no bytes move for
# PUSH_STALL_TIMEOUT, or if the whole transfer can't sustain
# PUSH_MIN_BYTES_PER_SEC on average (with a floor so small files always get a
# fair minimum).
PUSH_STALL_TIMEOUT = 120
PUSH_MIN_BYTES_PER_SEC = 262144  # ~0.25 MB/s
PUSH_TIMEOUT_FLOOR = 300

# After a stalled push's socket is closed, how much longer (on top of its own
# stall timeout, the mechanism actually expected to unblock it) to wait before
# giving up on the worker thread and just logging the leak.
PUSH_CANCEL_GRACE = 5

SCAN_INTERVAL_SECONDS = 86400  # 24 hours: periodic backfill rescan interval
PROCESS_DELAY_SECONDS = 60  # pacing delay between processing queued files

# Buffer for the tar file plus its extracted copy plus slack.
FREE_SPACE_SAFETY_FACTOR = 3

MEDIA_SCAN_BATCH_SIZE = 40  # files per `am broadcast` batch
KEYGUARD_SETTLE_SECONDS = 1.5  # wait after each interactivity-escalation step
BYTES_PER_KB = 1024  # `df -k` reports 1K blocks
