from __future__ import annotations

import os.path
from configparser import ConfigParser
from typing import TypedDict

from uploadrr.constants import CANDIDATES

DeviceConfig = TypedDict(
    "DeviceConfig",
    {"album_dir": str, "archive_dir": str, "import_dir": list[str], "serial": str},
)


class Config:
    def __init__(self) -> None:
        parser = ConfigParser()
        parser.read(CANDIDATES)

        self.album_root: str = parser["global"]["album_dir"]
        self.archive_root: str = parser["global"]["archive_dir"]
        self.data: list[DeviceConfig] = []
        self._archive_to_serial: dict[str, str] = {}

        for section_name in parser.sections():
            if "global" == section_name:
                continue

            album_dir = os.path.join(self.album_root, section_name)
            import_dirs = [
                iv.strip()
                for iv in parser[section_name]["import_dir"].split(",")
                if iv.strip()
            ]
            serial = parser[section_name]["serial"]
            archive_dir = os.path.join(self.archive_root, section_name)

            device_cfg: DeviceConfig = {
                "album_dir": album_dir,
                "archive_dir": archive_dir,
                "import_dir": import_dirs,
                "serial": serial,
            }
            self.data.append(device_cfg)
            self._archive_to_serial[archive_dir] = serial

    def get_album(self) -> str:
        return self.album_root

    def get_archive(self) -> str:
        return self.archive_root

    def get_data(self) -> list[DeviceConfig]:
        return self.data

    def get_serial(self, archive_dir: str) -> str:
        serial = self._archive_to_serial.get(archive_dir)
        if serial is not None:
            return serial
        raise KeyError("No serial for " + archive_dir)
