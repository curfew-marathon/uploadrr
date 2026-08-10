import os.path
from configparser import ConfigParser

from uploadrr.constants import CANDIDATES


class Config:
    def __init__(self):
        parser = ConfigParser()
        parser.read(CANDIDATES)

        self.album_root = parser["global"]["album_dir"]
        self.archive_root = parser["global"]["archive_dir"]
        self.data = []
        self._archive_to_serial = {}

        for section_name in parser.sections():
            if "global" == section_name:
                continue

            album_dir = os.path.join(self.album_root, section_name)
            import_dirs = [iv.strip() for iv in parser[section_name]["import_dir"].split(",") if iv.strip()]
            serial = parser[section_name]["serial"]
            archive_dir = os.path.join(self.archive_root, section_name)

            d = {
                "album": album_dir,
                "archive": archive_dir,
                "import": import_dirs,
                "serial": serial,
            }
            self.data.append(d)
            self._archive_to_serial[archive_dir] = serial

    def get_album(self):
        return self.album_root

    def get_archive(self):
        return self.archive_root

    def get_data(self):
        return self.data

    def get_serial(self, d):
        serial = self._archive_to_serial.get(d)
        if serial is not None:
            return serial
        raise KeyError("No serial for " + d)
