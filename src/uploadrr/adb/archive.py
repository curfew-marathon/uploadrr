from __future__ import annotations

import tarfile

from uploadrr.adb.transport import AdbError


def _archive_members(path: str) -> list[str]:
    """Validate a local tar archive and return its regular-file member names.

    Read locally rather than via the device's `tar -tf`, so link targets and
    path traversal can be checked directly instead of trusting names alone:
    a symlink member can point outside the extraction directory and pass a
    name-only check, then have later members extract through it.
    """
    names: list[str] = []
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            parts = member.name.split("/")
            if member.name.startswith("/") or ".." in parts:
                raise AdbError(f"Refusing archive: unsafe path {member.name!r}")
            if not (member.isfile() or member.isdir()):
                raise AdbError(
                    f"Refusing archive: {member.name!r} is a link or special file"
                )
            if member.isfile():
                names.append(member.name)
    if not names:
        raise AdbError(f"Archive {path} contains no regular files")
    return names
