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

## Prerequisites

- Python 3.x
- ADB (Android Debug Bridge) installed and accessible
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

1. Pull the Docker image:
```bash
docker pull curfewmarathon/uploadrr
```

2. Run the container with appropriate volume mounts:
```bash
docker run -v /path/to/config:/config \
           -v /path/to/archives:/archives \
           -v /path/to/albums:/albums \
           --net=host \
           curfewmarathon/uploadrr
```

#### Building from Source

Alternatively, you can build the image yourself:
```bash
docker build -t uploadrr .
```

## Configuration

Create a `config.ini` file in the root directory or in `/config/config.ini` with the following structure:

```ini
[global]
album_dir = /path/to/albums
archive_dir = /path/to/archives

[home]
serial = ABC123DEF456
import_dir = personal,photos

[work]
serial = XYZ789GHI012
import_dir = corporate
```

### Configuration Parameters

- **`album_dir`**: Root directory for album storage (e.g., `/path/to/albums`)
- **`archive_dir`**: Root directory where tar archives are monitored (e.g., `/path/to/archives`)
- **`import_dir`**: Comma-separated list of subdirectories under `archive_dir` to monitor for this device
- **`serial`**: Android device serial number (get with `adb devices`)

### Configuration Structure

The configuration uses a section-based approach:
- **`[global]`**: Contains shared settings for album and archive root directories
- **`[device_name]`**: Each device gets its own section (e.g., `[home]`, `[work]`) containing:
  - `serial`: The device's unique ADB serial number
  - `import_dir`: Subdirectories within the archive root to monitor for this device

For example, with the configuration above:
- Files in `/path/to/archives/personal/` and `/path/to/archives/photos/` will be uploaded to device `ABC123DEF456` (home)
- Files in `/path/to/archives/corporate/` will be uploaded to device `XYZ789GHI012` (work)

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

## File Processing Flow

```
Archive Directory → File Monitor → Queue → ADB Transfer → Extract → Cleanup → Google Photos
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
    └── listener.py        # File system event handler
```

## Dependencies

- **`pure-python-adb`**: Pure Python ADB client for device communication
- **`watchdog`**: File system monitoring library

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
docker run -e LOG_LEVEL=DEBUG curfewmarathon/uploadrr
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

## Error Handling

- **Storage Issues**: Automatically checks for sufficient free space before transfer
- **Device Connectivity**: Handles ADB connection errors gracefully
- **File Processing**: Continues processing other files if one fails
- **Configuration Errors**: Provides clear error messages for missing devices or configuration

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