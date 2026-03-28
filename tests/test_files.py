import os
from queue import Queue
from unittest.mock import patch
from uploadrr.files import add_files

def test_add_files(tmp_path):
    queue = Queue()
    (tmp_path / "test1.tar").write_text("data")
    (tmp_path / "test2.txt").write_text("data")
    os.mkdir(tmp_path / "subdir")

    add_files(str(tmp_path), queue)
    assert queue.get() == os.path.join(str(tmp_path), "test1.tar")
    assert queue.empty()

def test_add_files_not_found():
    queue = Queue()
    with patch('uploadrr.files.logger') as mock_logger:
        add_files("/non-existent-path", queue)
        mock_logger.warning.assert_called_with("Archive directory not found: %s", "/non-existent-path")
        assert queue.empty()
