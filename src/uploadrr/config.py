from __future__ import annotations

import os.path
from configparser import ConfigParser
from typing import TypedDict

from uploadrr.constants import CANDIDATES

DeviceConfig = TypedDict(
    "DeviceConfig",
    {"album": str, "archive": str, "import": list[str], "serial": str},
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

            d: DeviceConfig = {
                "album": album_dir,
                "archive": archive_dir,
                "import": import_dirs,
                "serial": serial,
            }
            self.data.append(d)
            self._archive_to_serial[archive_dir] = serial

    def get_album(self) -> str:
        return self.album_root

    def get_archive(self) -> str:
        return self.archive_root

    def get_data(self) -> list[DeviceConfig]:
        return self.data

    def get_serial(self, d: str) -> str:
        serial = self._archive_to_serial.get(d)
        if serial is not None:
            return serial
        raise KeyError("No serial for " + d)
