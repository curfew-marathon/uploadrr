import logging
import os

from ppadb.client import Client as AdbClient

from uploadrr.constants import STORAGE, DOWNLOAD, CAMERA

logger = logging.getLogger(__name__)

CLIENT = AdbClient(host="127.0.0.1", port=5037)


def get_device(serial):
    logger.debug("Connecting to device: %s", serial)
    device = CLIENT.device(serial)
    if device is None:
        raise IOError("Could not connect to device %s - check ADB connection" % serial)
    return device


def verify_free_space(device, file_size):
    logger.debug("Checking free space on device %s", device.serial)
    output = device.shell("df")
    splits = output.split("\n")
    for n in splits:
        if STORAGE in n:
            free = int(n.split()[3]) * 1000
            # Buffer for tar file plus extraction plus extra
            required_space = file_size * 3
            logger.debug("Device %s - Free space: %d bytes, Required: %d bytes", 
                        device.serial, free, required_space)
            if free < required_space:
                raise IOError("Device %s - Insufficient free space (free: %d, required: %d)" % 
                            (device.serial, free, required_space))
            logger.debug("Device %s - Storage check passed", device.serial)
            return

    raise IOError("Could not determine free storage for device %s" % device.serial)


def push_file(serial, file):
    logger.info("Starting transfer of %s to device %s", file, serial)
    device = get_device(serial)

    pre_work(device)
    file_size = os.stat(file).st_size
    logger.debug("File size: %d bytes", file_size)
    verify_free_space(device, file_size)

    file_dest = DOWNLOAD + os.path.basename(file)

    logger.info("Pushing file to device %s: %s -> %s", device.serial, file, file_dest)
    device.push(file, file_dest)
    logger.info("Extracting archive on device %s: %s", device.serial, file_dest)
    device.shell("tar -xf " + file_dest + " -C " + CAMERA)
    logger.info("Cleaning up temporary file on device %s: %s", device.serial, file_dest)
    device.shell("rm -f " + file_dest)
    logger.info("Launching Google Photos on device %s", device.serial)
    post_work(device)
    logger.info("Successfully completed transfer to device %s", device.serial)


def post_work(device):
    logger.debug("Waking device %s", device.serial)
    device.shell("input keyevent KEYCODE_WAKEUP")
    logger.debug("Starting Google Photos on device %s", device.serial)
    device.shell("monkey -p com.google.android.apps.photos -c android.intent.category.LAUNCHER 1")
    # TODO clean activity?


def pre_work(device):
    logger.debug("Stopping Google Photos on device %s", device.serial)
    device.shell("am force-stop com.google.android.apps.photos")
