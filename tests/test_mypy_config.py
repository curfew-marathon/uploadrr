import re
import subprocess
import sys


def test_mypy_resolves_src_package_as_uploadrr_not_src_uploadrr():
    """Regression: with explicit_package_bases=True and no mypy_path, mypy
    resolved src/uploadrr/*.py as module `src.uploadrr.*` instead of
    `uploadrr.*`. The per-module disallow_untyped_defs overrides in
    pyproject.toml are keyed on `uploadrr.*`, so under the old config they
    silently never matched anything - `mypy .` still reported success, but
    none of the strictness Phase 3 added was actually being enforced.

    Fixed by setting `mypy_path = ["src"]` (and dropping the now-redundant
    explicit_package_bases, which otherwise reintroduces a separate
    "Module has no attribute" error for test files importing `uploadrr`).
    """
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--verbose", "."],
        capture_output=True,
        text=True,
        check=False,
    )

    match = re.search(
        r"BuildSource\(path='\./src/uploadrr/config\.py', module='([\w.]+)'",
        result.stderr,
    )
    assert match is not None, (
        "could not find a BuildSource log line for src/uploadrr/config.py in "
        f"`mypy --verbose .` output:\n{result.stderr}"
    )
    assert match.group(1) == "uploadrr.config"
