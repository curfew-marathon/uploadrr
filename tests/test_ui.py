import logging

import pytest
from conftest import FakeRaw

from uploadrr.adb.transport import AdbCommandError, Device
from uploadrr.adb.ui import _ensure_interactive, _interactive, _media_scan


def test_media_scan_batches_and_joins_with_and():
    raw = FakeRaw()
    _media_scan(Device(raw), [f"/sdcard/DCIM/p{i}.jpg" for i in range(5)], batch=2)
    assert len(raw.shell_calls) == 3  # ceil(5 / 2)
    broadcasts = sum(c.count("MEDIA_SCANNER_SCAN_FILE") for c in raw.shell_calls)
    assert broadcasts == 5
    two_file_calls = [
        c for c in raw.shell_calls if c.count("MEDIA_SCANNER_SCAN_FILE") == 2
    ]
    assert two_file_calls and all(" && " in c for c in two_file_calls)


def test_media_scan_noop_without_paths():
    raw = FakeRaw()
    _media_scan(Device(raw), [])
    assert raw.shell_calls == []


def test_media_scan_percent_encodes_reserved_uri_characters():
    raw = FakeRaw()
    _media_scan(Device(raw), ["/sdcard/DCIM/a#b.jpg", "/sdcard/DCIM/c?d.jpg"], batch=2)
    cmd = raw.shell_calls[0]
    assert "file:///sdcard/DCIM/a%23b.jpg" in cmd
    assert "file:///sdcard/DCIM/c%3Fd.jpg" in cmd


def test_media_scan_propagates_broadcast_failure():
    raw = FakeRaw({"am broadcast": ("Broadcast completed: result=0", 1)})
    with pytest.raises(AdbCommandError):
        _media_scan(Device(raw), ["/sdcard/DCIM/p1.jpg"])


def test_interactive_true_only_on_affirmative_signals():
    raw = FakeRaw(
        {
            "dumpsys power": ("mWakefulness=Awake", 0),
            "dumpsys window": ("mShowingLockscreen=false", 0),
        }
    )
    assert _interactive(Device(raw)) is True


def test_interactive_treats_unknown_keyguard_output_as_locked():
    # Screen is awake but the keyguard grep matches nothing (renamed field,
    # unsupported dumpsys version, ...) - must not be read as "unlocked".
    raw = FakeRaw({"dumpsys power": ("mWakefulness=Awake", 0)})
    assert _interactive(Device(raw)) is False


def test_interactive_false_when_asleep_even_if_keyguard_clear():
    raw = FakeRaw({"dumpsys window": ("mShowingLockscreen=false", 0)})
    assert _interactive(Device(raw)) is False


def test_interactive_false_when_keyguard_explicitly_showing():
    # An explicit "true" signal (not just an unrecognized/empty one) must
    # also read as locked, even though the screen itself is awake.
    raw = FakeRaw(
        {
            "dumpsys power": ("mWakefulness=Awake", 0),
            "dumpsys window": ("mShowingLockscreen=true", 0),
        }
    )
    assert _interactive(Device(raw)) is False


def test_ensure_interactive_no_action_when_already_up():
    raw = FakeRaw(
        {
            "dumpsys power": ("mWakefulness=Awake", 0),
            "dumpsys window": ("mShowingLockscreen=false", 0),
            "dumpsys user": ("  mRunningUnlocked=true", 0),
        }
    )
    _ensure_interactive(Device(raw), settle=0)
    assert not any("keyevent" in c for c in raw.shell_calls)


def test_ensure_interactive_escalates_then_warns(caplog):
    caplog.set_level(logging.WARNING)
    raw = FakeRaw()  # every dumpsys returns "" -> never interactive, never awake
    _ensure_interactive(Device(raw), settle=0)
    joined = " ".join(raw.shell_calls)
    assert "KEYCODE_WAKEUP" in joined
    assert "dismiss-keyguard" in joined
    assert "keyevent 82" in joined
    assert "input touchscreen swipe" in joined
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("not confirmed interactive" in r.message for r in warnings)


def test_ensure_interactive_logs_info_when_awake_but_keyguard_unconfirmed(caplog):
    caplog.set_level(logging.INFO)
    # Awake, but the keyguard grep never matches - stays unconfirmed, not locked.
    raw = FakeRaw({"dumpsys power": ("mWakefulness=Awake", 0)})
    _ensure_interactive(Device(raw), settle=0)
    matches = [r for r in caplog.records if "not confirmed interactive" in r.message]
    assert matches
    assert all(r.levelno == logging.INFO for r in matches)


def test_ensure_interactive_warns_on_fbe_locked(caplog):
    caplog.set_level(logging.WARNING)
    raw = FakeRaw(
        {
            "dumpsys power": ("mWakefulness=Awake", 0),
            "dumpsys window": ("mShowingLockscreen=false", 0),
            "dumpsys user": ("mRunningUnlocked=false", 0),
        }
    )
    _ensure_interactive(Device(raw), settle=0)
    assert "not been unlocked since boot" in caplog.text
