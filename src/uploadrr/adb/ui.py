from __future__ import annotations

import logging
import re
import shlex
import time
from urllib.parse import quote

from uploadrr.adb import transport

logger = logging.getLogger(__name__)


def _screen_awake(device: transport.Device) -> bool:
    """True when `dumpsys power` reports the display is on."""
    power = device.sh(
        "dumpsys power | grep -E 'mWakefulness=|Display Power'", check=False
    )
    return "Awake" in power or "state=ON" in power


def _keyguard_clear(device: transport.Device) -> bool:
    """True only on an affirmative "no keyguard" signal from `dumpsys window`.

    Unrecognized or empty output (a failed pipe, a renamed field on a newer
    Android version) is treated as still locked, not as clear - otherwise an
    awake-but-locked device would look interactive and skip the escalation
    entirely.
    """
    keyguard = device.sh(
        "dumpsys window 2>/dev/null | "
        "grep -iE 'mShowingLockscreen|mDreamingLockscreen|KeyguardShowing'",
        check=False,
    ).lower()
    if "true" in keyguard:
        return False
    return "false" in keyguard  # explicit "not showing"; unrecognized output -> False


def _interactive(device: transport.Device) -> bool:
    """True when the screen is confirmed awake and the keyguard confirmed down."""
    return _screen_awake(device) and _keyguard_clear(device)


def _swipe_up(device: transport.Device) -> None:
    """Swipe up from near the bottom of the screen to dismiss a keyguard."""
    size = device.sh("wm size", check=False)
    m = re.search(r"(\d+)x(\d+)", size)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (1080, 2400)
    device.sh(
        f"input touchscreen swipe {w // 2} {int(h * 0.8)} {w // 2} {int(h * 0.2)} 200",
        check=False,
    )


def _ensure_interactive(device: transport.Device, settle: float = 1.5) -> None:
    """The target device has no secure lock, but its keyguard state varies:
    sometimes already interactive, sometimes screen-off, sometimes a non-secure
    keyguard is showing, sometimes it needs a swipe. Escalate until a `dumpsys`
    re-check confirms the device is interactive."""
    escalation = (
        lambda: device.sh("input keyevent KEYCODE_WAKEUP", check=False),
        lambda: device.sh("wm dismiss-keyguard", check=False),
        lambda: device.sh("input keyevent 82", check=False),  # MENU
        lambda: _swipe_up(device),
    )
    for step in escalation:
        if _interactive(device):
            break
        step()
        time.sleep(settle)
    else:
        if not _interactive(device):
            # Still worth proceeding (wake + swipe are harmless), but only
            # worth a warning if the screen itself never came on.
            log = logger.info if _screen_awake(device) else logger.warning
            log(
                "Device %s not confirmed interactive after escalation - "
                "proceeding anyway",
                device.serial,
            )


def _warn_if_never_unlocked(device: transport.Device) -> None:
    """Warn if the device hasn't been unlocked since boot (FBE) - Google
    Photos backup won't run until it is manually unlocked once. Unrelated to
    interactivity escalation; called separately from post_work(), right
    after _ensure_interactive()."""
    running = device.sh("dumpsys user | grep -i 'RunningUnlocked'", check=False).lower()
    if "false" in running:
        logger.warning(
            "Device %s has not been unlocked since boot (FBE) - Google Photos "
            "backup will not run until it is manually unlocked once.",
            device.serial,
        )


def _media_scan(device: transport.Device, paths: list[str], batch: int = 40) -> None:
    """Broadcast MEDIA_SCANNER_SCAN_FILE for each extracted path in batches."""
    if not paths:
        logger.debug("No extracted files to scan on device %s", device.serial)
        return
    for i in range(0, len(paths), batch):
        chunk = paths[i : i + batch]
        # `&&`, not `;`: a broadcast that fails to dispatch at all is a real
        # sign the scan mechanism is broken on this device, so it should
        # propagate rather than be hidden behind the last command's status.
        # (Note `am broadcast` still exits 0 even when a receiver ignores the
        # intent - this only catches failures to dispatch in the first place.)
        cmd = " && ".join(
            "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
            f"-d {shlex.quote('file://' + quote(p, safe='/'))}"
            for p in chunk
        )
        device.sh(cmd)
    logger.info(
        "Requested media scan of %d files on device %s", len(paths), device.serial
    )
