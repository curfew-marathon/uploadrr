import pytest
import time
from queue import Queue
from unittest.mock import MagicMock
from uploadrr.listener import MonitorFolder

def test_on_closed_tar():
    queue = Queue()
    handler = MonitorFolder(queue)
    event = MagicMock(src_path="test.tar")
    handler.on_closed(event)
    assert queue.get() == "test.tar"
    assert "test.tar" in handler.processed_files

def test_on_closed_non_tar():
    queue = Queue()
    handler = MonitorFolder(queue)
    event = MagicMock(src_path="test.txt")
    handler.on_closed(event)
    assert queue.empty()

def test_on_closed_already_processed():
    queue = Queue()
    handler = MonitorFolder(queue)
    handler.processed_files.add("test.tar")
    event = MagicMock(src_path="test.tar")
    handler.on_closed(event)
    assert queue.empty()

def test_on_closed_debounce():
    queue = Queue()
    handler = MonitorFolder(queue)
    event = MagicMock(src_path="test.tar")

    # First call: OK (last_time will be 0.0)
    handler.on_closed(event)
    assert queue.get() == "test.tar"

    handler.processed_files.clear()

    # Manually set last_event_time to simulate rapid calls and test debounce
    handler.last_event_time["test.tar"] = time.time()
    handler.on_closed(event)
    assert queue.empty()
