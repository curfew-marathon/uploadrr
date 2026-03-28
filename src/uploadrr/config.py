import os.path
from configparser import ConfigParser

from uploadrr.constants import CANDIDATES


class Config:

    def __init__(self):
        parser = ConfigParser()
        parser.read(CANDIDATES)

        self.album_root = parser['global']['album_dir']
        self.archive_root = parser['global']['archive_dir']
        self.data = []

        for section_name in parser.sections():
            if 'global' == section_name:
                continue

            album_dir = os.path.join(self.album_root, section_name)
            import_value = parser[section_name]['import_dir'].split(',')
            serial = parser[section_name]['serial']

            import_value = [iv.strip() for iv in import_value if iv.strip()]

            # Use import directory as sub-path as specified in README
            for iv in import_value:
                archive_dir = os.path.join(self.archive_root, iv)
                # Note: album dir isn't specified to have import_dir as a sub-path in README, but
                # keep original logic for album directory just using section_name for backward compat
                d = {'album': album_dir, 'archive': archive_dir, 'import': iv, 'serial': serial}
                self.data.append(d)

    def get_album(self):
        return self.album_root

    def get_archive(self):
        return self.archive_root

    def get_data(self):
        return self.data

    def get_serial(self, d):
        for data_dict in self.data:
            if d == data_dict.get('archive'):
                return data_dict.get('serial')
        raise KeyError("No serial for " + d)
