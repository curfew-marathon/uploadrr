import sys
from unittest.mock import MagicMock
import pytest

sys.modules['ppadb'] = MagicMock()
sys.modules['ppadb.client'] = MagicMock()

from uploadrr.adb import get_device

def test_get_device_ioerror(monkeypatch):
    mock_client = MagicMock()
    mock_client.device.return_value = None
    monkeypatch.setattr("uploadrr.adb.CLIENT", mock_client)
    with pytest.raises(IOError, match="Could not connect to device test_serial"):
        get_device("test_serial")

def test_get_device_success(monkeypatch):
    mock_client = MagicMock()
    mock_client.device.return_value = "mock_device"
    monkeypatch.setattr("uploadrr.adb.CLIENT", mock_client)
    assert get_device("test_serial") == "mock_device"
    mock_client.device.assert_called_once_with("test_serial")
