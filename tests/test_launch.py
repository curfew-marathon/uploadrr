import runpy


def test_launch_module_starts_metrics_and_files_when_run_as_main(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "true")
    start_calls = []
    launch_calls = []
    monkeypatch.setattr("uploadrr.metrics.start", lambda port: start_calls.append(port))
    monkeypatch.setattr("uploadrr.files.launch", lambda: launch_calls.append(True))
    runpy.run_module("uploadrr.launch", run_name="__main__")
    assert start_calls == ["9120"]
    assert launch_calls == [True]


def test_launch_module_skips_metrics_when_disabled(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "false")
    start_calls = []
    monkeypatch.setattr("uploadrr.metrics.start", lambda port: start_calls.append(port))
    monkeypatch.setattr("uploadrr.files.launch", lambda: None)
    runpy.run_module("uploadrr.launch", run_name="__main__")
    assert start_calls == []
