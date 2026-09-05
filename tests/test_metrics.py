from queue import Queue
from unittest.mock import patch

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


def test_start_calls_start_http_server_with_port():
    with patch("uploadrr.metrics.start_http_server") as mock_start:
        metrics.start(9200)
    mock_start.assert_called_once_with(9200)


def test_start_swallows_port_collision():
    with (
        patch(
            "uploadrr.metrics.start_http_server",
            side_effect=OSError("Address already in use"),
        ),
        patch("uploadrr.metrics.logger") as mock_logger,
    ):
        metrics.start(9200)  # must not raise
    mock_logger.error.assert_called_once()
