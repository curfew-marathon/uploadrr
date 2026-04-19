import pytest
import os
from unittest.mock import patch
from uploadrr.config import Config


def test_config_methods(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[global]\nalbum_dir = /albums\narchive_dir = /archives\n\n[device1]\nimport_dir = camera1, camera2\nserial = serial123"
    )

    with patch("uploadrr.config.CANDIDATES", [str(config_file)]):
        config = Config()
        assert config.get_archive() == "/archives"
        data = config.get_data()
        assert len(data) == 2
        assert data[0]["import"] == "camera1"
        assert data[1]["serial"] == "serial123"

        # Test get_serial
        cam1_path = os.path.join("/archives", "camera1")
        assert config.get_serial(cam1_path) == "serial123"

        with pytest.raises(KeyError, match="No serial for /invalid"):
            config.get_serial("/invalid")
