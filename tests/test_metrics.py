from queue import Queue
from unittest.mock import MagicMock

from uploadrr import metrics


def test_bind_queue_depth_tracks_queue_size():
    q = Queue()
    metrics.bind_queue_depth(q)
    assert metrics.QUEUE_DEPTH.collect()[0].samples[0].value == 0

    q.put("a")
    q.put("b")
    assert metrics.QUEUE_DEPTH.collect()[0].samples[0].value == 2

    q.get()
    assert metrics.QUEUE_DEPTH.collect()[0].samples[0].value == 1


def test_start_calls_start_http_server_with_port(monkeypatch):
    mock_start = MagicMock()
    monkeypatch.setattr("uploadrr.metrics.start_http_server", mock_start)
    metrics.start(9120)
    mock_start.assert_called_once_with(9120)


def test_start_swallows_port_collision(monkeypatch):
    monkeypatch.setattr(
        "uploadrr.metrics.start_http_server",
        MagicMock(side_effect=OSError("Address already in use")),
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("uploadrr.metrics.logger", mock_logger)
    metrics.start(9120)  # must not raise
    mock_logger.error.assert_called_once()


def test_start_swallows_non_numeric_port(monkeypatch):
    mock_logger = MagicMock()
    monkeypatch.setattr("uploadrr.metrics.logger", mock_logger)
    metrics.start("abc")  # must not raise
    mock_logger.error.assert_called_once()


def test_start_swallows_out_of_range_port(monkeypatch):
    # Not mocked: an out-of-range port raises OverflowError from the real
    # socket bind inside start_http_server, not from int() itself.
    mock_logger = MagicMock()
    monkeypatch.setattr("uploadrr.metrics.logger", mock_logger)
    metrics.start("99999999")  # must not raise
    mock_logger.error.assert_called_once()
