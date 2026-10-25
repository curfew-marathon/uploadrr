import os
from unittest.mock import patch

import pytest

from uploadrr.config import Config


def test_config_methods(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[global]\nalbum_dir = /albums\narchive_dir = /archives\n\n[device1]\nimport_dir = camera1, camera2\nserial = serial123"
    )

    with patch("uploadrr.config.CANDIDATES", [str(config_file)]):
        config = Config()
        assert config.get_album() == "/albums"
        assert config.get_archive() == "/archives"
        data = config.get_data()
        assert len(data) == 1
        assert data[0]["import_dir"] == ["camera1", "camera2"]
        assert data[0]["serial"] == "serial123"

        # Test get_serial — archive path is keyed by section name, not import_dir
        device1_path = os.path.join("/archives", "device1")
        assert config.get_serial(device1_path) == "serial123"

        with pytest.raises(KeyError, match="No serial for /invalid"):
            config.get_serial("/invalid")


def test_config_multiple_devices_and_filters_blank_import_dirs(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[global]\nalbum_dir = /albums\narchive_dir = /archives\n\n"
        "[device1]\nimport_dir = camera1, ,camera2\nserial = serial123\n\n"
        "[device2]\nimport_dir = camera3\nserial = serial456\n"
    )

    with patch("uploadrr.config.CANDIDATES", [str(config_file)]):
        config = Config()
        data = config.get_data()

        assert len(data) == 2
        assert data[0]["import_dir"] == ["camera1", "camera2"]
        assert data[1]["import_dir"] == ["camera3"]
        assert config.get_serial(os.path.join("/archives", "device1")) == "serial123"
        assert config.get_serial(os.path.join("/archives", "device2")) == "serial456"
