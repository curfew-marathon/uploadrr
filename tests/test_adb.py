import sys
from unittest.mock import MagicMock
import pytest

sys.modules['ppadb'] = MagicMock()
sys.modules['ppadb.client'] = MagicMock()

from uploadrr.adb import get_device, pre_work

def test_pre_work():
    mock_device = MagicMock()
    pre_work(mock_device)
    mock_device.shell.assert_called_once_with("am force-stop com.google.android.apps.photos")

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


def test_push_file_quotes_shell_arguments(monkeypatch):
    from uploadrr.adb import push_file

    mock_device = MagicMock()
    mock_device.serial = "test_serial"
    monkeypatch.setattr("uploadrr.adb.get_device", lambda s: mock_device)
    monkeypatch.setattr("uploadrr.adb.pre_work", lambda d: None)
    monkeypatch.setattr("uploadrr.adb.post_work", lambda d: None)
    monkeypatch.setattr("uploadrr.adb.verify_free_space", lambda d, s: None)

    # Mock os.stat and os.path.basename
    class MockStat:
        st_size = 100

    monkeypatch.setattr("os.stat", lambda f: MockStat())
    monkeypatch.setattr("os.path.basename", lambda f: 'file with spaces and "quotes".tar')

    push_file("test_serial", "/fake/path/file with spaces and \"quotes\".tar")

    # Check if shell was called with quoted arguments
    import shlex
    from uploadrr.constants import CAMERA, DOWNLOAD
    expected_file_dest = DOWNLOAD + 'file with spaces and "quotes".tar'

    calls = mock_device.shell.call_args_list
    assert any(
        f"tar -xf {shlex.quote(expected_file_dest)} -C {shlex.quote(CAMERA)}" in call[0][0]
        for call in calls
    )
    assert any(
        f"rm -f {shlex.quote(expected_file_dest)}" in call[0][0]
        for call in calls
    )
