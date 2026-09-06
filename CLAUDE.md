# CLAUDE.md

Guidance for working in this repo. Keep it short; it is not a substitute for the README.

## What uploadrr does

Watches `archive_dir/<section>/` for `.tar` files, pushes each to an Android device
over ADB, extracts it into `/sdcard/DCIM/`, triggers a media scan, and deletes the
source tar only after the whole sequence succeeds.

## Architecture gotcha

The container has **no adb binary**. `adb.py` uses ppadb (`pure-python-adb`) to talk
to an adb **server** over TCP at `127.0.0.1:5037`, reachable only because the
container runs with `--net=host`. ppadb can *use* an adb server, never *start* one,
so the adb server is a host dependency, managed by `deploy/adb-server.service`. A
dead adb server stalls every transfer until it is back; uploadrr cannot recover it
itself.

## Load-bearing invariants

- **A failed transfer must never delete the source tar.** `files.py` deliberately
  keeps failed files for retry (next new tar, the 24h periodic scan, or a restart).
  Do not add cleanup to error paths.
- **The device layer is hardened on purpose.** Every `adb.py` call has a per-call
  timeout and converts hangs, disconnects, and non-zero shell exits into `AdbError`
  (a subclass of `OSError`, so callers keep the tar). Preserve this when editing;
  see PRs #26 and #27 for the rationale.
- Archives are validated locally before anything is pushed (`_archive_members`):
  no absolute paths, no `..`, no links or special files.

## Commands

Matches CI (`.github/workflows/ci.yml`, Python 3.11):

    ruff check .
    ruff format --check .        # tests/test_adb.py is excluded from formatting (ruff.toml)
    mypy .
    pytest                       # CI runs: pytest --cov=. --cov-report=term-missing

## Config and deployment

- `config.ini` lives at the repo root for local runs, or `/config/config.ini` in
  the container (`constants.CANDIDATES`). `album_dir` / `archive_dir` are paths as
  seen inside the container.
- `docker-compose.yml` reads host-specific values from a gitignored `.env`
  (template: `.env.example`). Never commit real host paths, timezones, or device
  serials.
- `PUID` / `PGID` are passed through but currently do nothing (plain `python:alpine`
  base, no s6 init); the process runs as root.

## Conventions

- No em dashes in code, comments, commit messages, or PR text.
- Match the surrounding style: these modules favor thorough docstrings that explain
  *why* a workaround exists.
