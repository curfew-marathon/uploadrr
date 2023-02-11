import logging
import os

from ppadb.client import Client as AdbClient

from uploadrr.constants import STORAGE, DOWNLOAD, CAMERA

logger = logging.getLogger(__name__)

CLIENT = AdbClient(host="127.0.0.1", port=5037)


def get_device(serial):
    return CLIENT.device(serial)


def verify_free_space(device, file_size):
    output = device.shell("df")
    splits = output.split("\n")
    for n in splits:
        if STORAGE in n:
            free = int(n.split()[3]) * 1000
            # Buffer for tar file plus extraction plus extra
            if free < file_size * 3:
                raise IOError("Device " + device.serial + " - Not enough free space [free " + str(free) + "]")
            return

    raise IOError("Could not determine free storage for " + device.serial)


def push_file(serial, file):
    device = get_device(serial)
    file_size = os.stat(file).st_size
    verify_free_space(device, file_size)
    file_dest = DOWNLOAD + os.path.basename(file)

    logger.info("Device " + device.serial + " - Push " + file)
    device.push(file, file_dest)
    logger.info("Device " + device.serial + " - Extract " + file_dest)
    device.shell("tar -xf " + file_dest + " -C " + CAMERA)
    logger.info("Device " + device.serial + " - Remove the tar " + file_dest)
    device.shell("rm -f " + file_dest)

    # TODO start photos?
    # TODO clean activity?
