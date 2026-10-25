import tarfile

import pytest
from conftest import _make_tar

from uploadrr.adb.archive import _archive_members
from uploadrr.adb.transport import AdbError


def test_archive_members_returns_regular_file_names(tmp_path):
    tar = _make_tar(tmp_path, "a.tar", [("a.jpg", b"1"), ("dir/b.jpg", b"22")])
    assert _archive_members(str(tar)) == ["a.jpg", "dir/b.jpg"]


@pytest.mark.parametrize("bad_name", ["../x.jpg", "dir/../../x.jpg", ".."])
def test_archive_members_rejects_parent_dir_components(tmp_path, bad_name):
    tar = _make_tar(tmp_path, "a.tar", [(bad_name, b"1")])
    with pytest.raises(AdbError, match="unsafe path"):
        _archive_members(str(tar))


def test_archive_members_rejects_absolute_path(tmp_path):
    tar = _make_tar(tmp_path, "a.tar", [("/etc/passwd", b"1")])
    with pytest.raises(AdbError, match="unsafe path"):
        _archive_members(str(tar))


def test_archive_members_rejects_symlink(tmp_path):
    tar = _make_tar(tmp_path, "a.tar", [("link", None)])
    with pytest.raises(AdbError, match="link or special file"):
        _archive_members(str(tar))


def test_archive_members_rejects_empty_archive(tmp_path):
    path = tmp_path / "empty.tar"
    with tarfile.open(path, "w"):
        pass
    with pytest.raises(AdbError, match="no regular files"):
        _archive_members(str(path))
