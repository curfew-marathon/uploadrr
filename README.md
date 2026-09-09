# uploadrr

Uploadrr is an automated media file transfer tool that monitors archive directories for tar files containing media content and automatically uploads them to Android devices via ADB (Android Debug Bridge). Once successfully uploaded and extracted, the archive files are automatically deleted from the source system.

## Features

- 📁 **Automatic file monitoring**: Watches specified directories for new `.tar` archive files
- 📱 **Android device integration**: Uses ADB to transfer files to Android devices
- 🔄 **Automatic extraction**: Extracts tar archives directly to the device's camera folder (`/sdcard/DCIM/`)
- 🧹 **Auto-cleanup**: Removes source archive files after successful upload
- 📸 **Google Photos integration**: Automatically launches Google Photos after file transfer
- 💾 **Storage validation**: Checks available storage space before transfer
- 🔧 **Multi-device support**: Can handle multiple Android devices with different configurations
- 🐳 **Docker support**: Includes Dockerfile for containerized deployment
- 📊 **Prometheus metrics**: Exposes queue depth, transfer throughput, and failure rates over HTTP

## Prerequisites

- Python 3.x
- ADB (Android Debug Bridge) installed and accessible. The Docker image does **not**
  bundle adb - it talks to an adb server running on the host (see [ADB Server Setup](#adb-server-setup))
- Android device(s) with:
  - USB debugging enabled
  - Connected via USB or wireless ADB
  - Google Photos app installed (optional but recommended)

## Installation

### Local Installation

1. Clone the repository:
```bash
git clone https://github.com/curfew-marathon/uploadrr.git
cd uploadrr
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create a configuration file (see Configuration section below)

4. Run the application:
```bash
python src/launch.py
```

### Docker Installation

#### Docker Compose (recommended)

A [`docker-compose.yml`](docker-compose.yml) is included. It reads the host paths,
`TZ`, and the optional `LOG_LEVEL` / `METRICS_*` settings from `.env`, each with a
default, so an empty `.env` still brings the stack up.

1. Create your environment file:
```bash
cp .env.example .env
$EDITOR .env
```
Set `UPLOADRR_DATA_DIR` to the host directory that is mounted at `/data`: it must be
the parent of the `album_dir` / `archive_dir` roots in your `config.ini`. With the
shipped `config/config.ini` (both roots are `/data`) that is the directory uploadrr
watches directly; if your `config.ini` uses `/data/archives`, point
`UPLOADRR_DATA_DIR` at the directory *containing* `archives/`, not at `archives/`
itself. Set `UPLOADRR_CONFIG_DIR` if your `config.ini` lives outside `./config`.
Everything else is optional. `.env` is gitignored and no real paths are committed;
the compose file falls back to a repository-local `./config` and `./data` bind mount.

2. Set up the host adb server (see [ADB Server Setup](#adb-server-setup)).

3. Start and stop with the helper scripts:
```bash
./start.sh          # ensures the adb server is up, builds the image, then starts
./start.sh --pull   # run the published image instead of building (server default)
./start.sh --no-build  # run whatever image is already present
./start.sh --logs   # follow logs once healthy
./start.sh --no-adb # skip the adb server check
./stop.sh           # `docker compose down` (leaves the adb server running)
./stop.sh --adb     # also stops the adb server
./stop.sh --images  # also removes the built/pulled image
./stop.sh --volumes # also removes Compose volumes after a prompt (--yes skips it)
```
Default `./start.sh` builds from source so you run exactly what is in your tree;
`--pull` is the explicit opt-in to the published
`ghcr.io/curfew-marathon/uploadrr` image (the server passes it on every start).
`./start.sh` creates `.env` from the example if missing, validates the compose
file, and waits for the container's healthcheck.

#### Docker run

1. Pull the Docker image:
```bash
docker pull ghcr.io/curfew-marathon/uploadrr:latest
```

2. Run the container with appropriate volume mounts:
```bash
docker run -v /path/to/config:/config \
           -v /path/to/data:/data \
           --net=host \
           ghcr.io/curfew-marathon/uploadrr:latest
```

#### Building from Source

Alternatively, build the image yourself under the name Compose expects:
```bash
docker compose build
```
Use `docker compose build`, not `docker build -t uploadrr .`: the compose file
references `ghcr.io/curfew-marathon/uploadrr:${UPLOADRR_TAG:-latest}`, so a plain
`uploadrr:latest` tag would not be picked up by `./start.sh --no-build`.

## ADB Server Setup

The uploadrr container has **no adb binary of its own**. It uses a pure-Python adb
client that connects to an adb **server** over TCP - by default `127.0.0.1:5037`,
reachable because the container runs with `--net=host`. That server must be running
on the host, or every transfer fails with:

```
adb server unreachable for <serial>: ERROR: connecting to 127.0.0.1:5037 [Errno 111] Connection refused
```

The Python client can only *use* an adb server; it can never *start* one. So if the
host's adb server stops, uploadrr cannot recover on its own - the tar files simply
accumulate and wait for retry until the server is back.

### Recommended: run adb as a supervised service

A ready-to-use systemd unit is provided at [`deploy/adb-server.service`](deploy/adb-server.service).
Install it as a **user** service:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/adb-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now adb-server.service

# REQUIRED: without linger, systemd stops your user manager (and this
# service with it) when your last login session ends - the adb server
# then dies on logout and only comes back on your next login.
sudo loginctl enable-linger "$USER"
```

Verify it is running in the foreground (supervised), not just "exited":

```bash
systemctl --user status adb-server.service   # want: active (running)
loginctl show-user "$USER" -p Linger          # want: Linger=yes
adb devices                                   # target device should be listed
```

The unit runs `adb nodaemon server` in the foreground and sets `Restart=always`, so an
adb crash or a version-mismatch kill self-heals within a few seconds. It can also be
installed as a system service - see the comments in the unit file.

### Manual alternative

If you just run `adb start-server` by hand, be aware it will not survive a reboot, a
crash, or (for a login-session server) your logout. Prefer the service above for any
unattended deployment.

## Configuration

Create a `config.ini` file in the root directory or in `/config/config.ini` with the following structure:

```ini
[global]
album_dir = /data
archive_dir = /data

[home]
serial = ABC123DEF456
import_dir = personal,photos

[work]
serial = XYZ789GHI012
import_dir = corporate
```

`album_dir` and `archive_dir` are paths **as seen inside the container**. With the
provided `docker-compose.yml`, `UPLOADRR_DATA_DIR` is bound to `/data`; the shipped
`config/config.ini` uses `/data` for both roots so uploadrr watches that directory
directly. Split them into subdirectories (e.g. `/data/albums`, `/data/archives`)
only if your host keeps albums and archives apart, and point `UPLOADRR_DATA_DIR` at
their common parent.

### Configuration Parameters

- **`album_dir`**: Root directory for album storage (container path, e.g. `/data`)
- **`archive_dir`**: Root directory where tar archives are monitored (container path, e.g. `/data`)
- **`import_dir`**: Comma-separated list of subdirectories under `album_dir/[section_name]/` where photos are placed for importrr to process. Not used by uploadrr directly - uploadrr watches `archive_dir/[section_name]/` for the tar files that importrr produces.
- **`serial`**: Android device serial number (get with `adb devices`)

### Configuration Structure

The configuration uses a section-based approach:
- **`[global]`**: Contains shared settings for album and archive root directories
- **`[device_name]`**: Each device gets its own section (e.g., `[home]`, `[work]`) containing:
  - `serial`: The device's unique ADB serial number
  - `import_dir`: Subdirectories under `album_dir/[section_name]/` where importrr picks up photos to process and archive

For example, with the configuration above:
- importrr reads photos from `/data/home/personal/` and `/data/home/photos/`, archives them as tar files to `/data/home/`
- uploadrr watches `/data/home/` and `/data/work/` and pushes tar files to the matching device

### Getting Device Serial Numbers

To find your Android device serial numbers:

```bash
adb devices
```

This will list all connected devices with their serial numbers.

## How It Works

1. **Monitoring**: The application uses Python's `watchdog` library to monitor specified archive directories for new `.tar` files
2. **Queue Processing**: When a tar file is detected (on file close), it's added to a processing queue
3. **Device Selection**: Based on the file's location, the corresponding Android device is identified
4. **Storage Check**: Verifies the target device has sufficient free space (3x the file size for buffer)
5. **File Transfer**: Uses ADB to push the tar file to the device's Download folder (`/sdcard/Download/`)
6. **Extraction**: Extracts the tar contents directly to the camera folder (`/sdcard/DCIM/`)
7. **Cleanup**: Removes the tar file from both the device and the source system
8. **App Launch**: Starts Google Photos to process the new media files

## Error Handling and Recovery

- **Failed Transfers**: Files that fail to process (due to storage issues, device unavailability, etc.) remain in the source directory
- **Periodic Recovery**: Every 24 hours, the application performs a full directory scan to retry any failed files
- **Storage Issues**: Automatically checks for sufficient free space before transfer
- **Device Connectivity**: Handles ADB connection errors gracefully; a lost device or a
  stopped adb server surfaces as a per-file error and the file is retried later. Note
  that uploadrr cannot restart a stopped adb server itself - see [ADB Server Setup](#adb-server-setup)
- **File Processing**: Continues processing other files if one fails
- **Configuration Errors**: Provides clear error messages for missing devices or configuration

### Recovering a backlog after an adb outage

Files stranded while the adb server was down are only re-queued on the next new tar or
the 24-hour periodic scan. To reprocess them immediately, restart the container - on
startup uploadrr scans the archive directories and queues every existing `.tar`:

```bash
docker restart <container-name>
```

## File Processing Flow

```
Archive Directory → File Monitor → Queue → ADB Transfer → Extract → Cleanup → Google Photos
                     ↑                                        ↓
              24-hour periodic scan ← ← ← ← ← Failed files remain
```

## Directory Structure

```
src/
├── launch.py              # Main entry point
└── uploadrr/
    ├── __init__.py        # Package initialization
    ├── adb.py             # ADB device communication
    ├── config.py          # Configuration file parser
    ├── constants.py       # Application constants
    ├── files.py           # File monitoring and processing
    ├── listener.py        # File system event handler
    └── metrics.py         # Prometheus metrics
```

## Dependencies

- **`pure-python-adb`**: Pure Python ADB client for device communication
- **`watchdog`**: File system monitoring library
- **`prometheus-client`**: Prometheus metrics exposition

## Logging

The application provides comprehensive logging with configurable levels:

### Log Levels
- **DEBUG**: Detailed diagnostic information (file scanning, device connections, storage checks)
- **INFO**: General operational information (file processing, transfers, startup/shutdown)
- **WARNING**: Important events that may need attention (missing device configs, file type issues)
- **ERROR**: Error conditions that prevent file processing

### Configuration
Set the logging level using the `LOG_LEVEL` environment variable:
```bash
# For production (default)
export LOG_LEVEL=INFO
python src/launch.py

# For debugging
export LOG_LEVEL=DEBUG
python src/launch.py

# Docker example
docker run -e LOG_LEVEL=DEBUG ghcr.io/curfew-marathon/uploadrr:latest
```

### Log Format
Logs include timestamps, logger names, levels, and structured messages:
```
2025-08-03 14:30:15 - uploadrr.files - INFO - Starting uploadrr - setting up file observers
2025-08-03 14:30:16 - uploadrr.files - INFO - Processing new file: /data/personal/photos.tar
2025-08-03 14:30:17 - uploadrr.adb - INFO - Starting transfer of /data/personal/photos.tar to device ABC123DEF456
```

### Noise Reduction
The application automatically reduces verbose logging from:
- **Watchdog internal events**: File system modification events are filtered to WARNING level
- **Duplicate processing**: Files are debounced to prevent processing the same file multiple times
- **Event spam**: Rapid file modification events are debounced with a 2-second window

## Metrics

Uploadrr exposes Prometheus-format metrics over HTTP for monitoring queue depth, transfer
throughput, and failure rates.

### Configuration
Set these environment variables to control the metrics endpoint:
- `METRICS_ENABLED` (default `true`): set to `false` to disable the metrics server entirely
- `METRICS_PORT` (default `9120`): port the `/metrics` endpoint listens on

```bash
# Docker example
docker run -e METRICS_PORT=9120 ghcr.io/curfew-marathon/uploadrr:latest
```

With `--net=host` (the mode this project documents for reaching the host's adb server), the
metrics port is already reachable at `<host>:9120` directly - no `-p` mapping needed.

### Metrics Exposed
| Metric | Type | Labels | Description |
|---|---|---|---|
| `uploadrr_queue_depth` | Gauge | - | Files waiting to be processed |
| `uploadrr_files_processed_total` | Counter | `outcome` | Files processed, by outcome (`success`, `no_device_config`, `os_error`, `unexpected_error`) |
| `uploadrr_file_processing_seconds` | Histogram | - | Time to process one file (push + extract + cleanup) |
| `uploadrr_push_seconds` | Histogram | `serial` | Time to push and extract one archive on a device |
| `uploadrr_push_bytes_total` | Counter | `serial` | Bytes successfully pushed to a device |
| `uploadrr_push_failures_total` | Counter | `serial` | Failed transfer attempts, by device |

## Error Handling

- **Failed Transfers**: Files that fail to process (due to storage issues, device unavailability, etc.) remain in the source directory for automatic retry
- **Periodic Recovery**: Every 24 hours, the application performs a full directory scan to retry any failed files
- **Storage Issues**: Automatically checks for sufficient free space before transfer
- **Device Connectivity**: Handles ADB connection errors gracefully  
- **File Processing**: Continues processing other files if one fails
- **Configuration Errors**: Provides clear error messages for missing devices or configuration
- **Automatic Retry**: Failed files are automatically retried during the next 24-hour scan cycle

## Use Cases

- **Photography Workflows**: Automatically transfer processed photo archives to mobile devices
- **Media Backup**: Distribute media content across multiple Android devices
- **Content Distribution**: Automated deployment of media files to Android devices
- **Development Testing**: Quickly deploy test media content to development devices

## Limitations

- Only supports tar archive format
- Requires ADB access to target devices
- Designed specifically for Android devices
- Assumes Google Photos app for media processing

## Contributing

Contributions are welcome! Please feel free to submit issues, feature requests, or pull requests.

## License

This project is licensed under the terms specified in the LICENSE file.

## Support

For issues or questions, please create an issue in the GitHub repository.