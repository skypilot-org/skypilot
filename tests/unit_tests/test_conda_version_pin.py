"""Guards against conda-installer version drift and known-bad pins.

The Miniconda installer version is pinned in several places that must stay in
sync. An outdated pin ships stale transitive dependencies (e.g. SQLite) that
security scanners flag as CVEs on every cluster instance.

This test covers the pins that are on the current version. Some pins (the VM
image provisioner ``skypilot.sh`` and the docs custom-image recipe) are
intentionally not covered yet — they are handled in a follow-up that removes
conda from the VM images entirely.

TODO(follow-up): fold ``sky/catalog/images/provisioners/skypilot.sh`` and the
docs recipe back into this guard once the VM images stop bundling conda.
"""
import pathlib
import re

# The version that shipped the CVEs this guard exists to prevent. No pin should
# regress back to it.
KNOWN_BAD_VERSION = 'py310_23.11.0-2'

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Files that must all pin the same, current conda-installer version.
_PINNED_FILES = [
    _REPO_ROOT / 'sky' / 'skylet' / 'constants.py',
    _REPO_ROOT / 'tests' / 'smoke_tests' / 'docker' / 'Dockerfile_test',
]

_VERSION_RE = re.compile(r'Miniconda3-(py3\d+_[\d.]+-\d+)-Linux')


def _extract_versions(path: pathlib.Path):
    return set(_VERSION_RE.findall(path.read_text()))


def test_conda_pins_are_consistent():
    """All covered files pin the exact same conda-installer version."""
    versions_by_file = {path: _extract_versions(path) for path in _PINNED_FILES}
    for path, versions in versions_by_file.items():
        assert versions, (
            f'No Miniconda3 version string found in {path}; the pin format may '
            f'have changed — update {__file__}.')

    all_versions = set().union(*versions_by_file.values())
    assert len(all_versions) == 1, (
        'Conda-installer versions are out of sync across files: '
        f'{ {str(p): sorted(v) for p, v in versions_by_file.items()} }')


def test_conda_pin_is_not_known_bad():
    """No covered file regresses to the CVE-ridden version."""
    for path in _PINNED_FILES:
        versions = _extract_versions(path)
        assert KNOWN_BAD_VERSION not in versions, (
            f'{path} pins the known-bad conda version {KNOWN_BAD_VERSION}, '
            'which ships CVE-flagged dependencies. Bump it to a current build.')
