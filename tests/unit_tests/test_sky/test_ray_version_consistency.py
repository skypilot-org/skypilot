"""Tests that every place pinning a Ray version agrees with the constant.

The Ray version lives in one place (``SKY_REMOTE_RAY_VERSION``) but is also
baked into images, referenced by the docs, and used to guard the patch step.
A stale copy is easy to miss and fails silently: the Kubernetes patch guard,
for example, is an ``&&`` chain that simply skips the Ray patches when the
version does not match, without any error.
"""
import os
import re

import pytest

from sky.skylet import constants

# tests/unit_tests/test_sky -> repo root
ROOT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

RAY_PATCHES_DIR = os.path.join(ROOT_DIR, 'sky', 'skylet', 'ray_patches')

_PIP_PIN = r"ray\[default\]==([0-9][^'\"\s]*)"
_SH_PIN = r'RAY_VERSION=([0-9][^\s]*)'
# Prose, e.g. "Ray version is set to ``2.56.1`` in ``SKY_REMOTE_RAY_VERSION``".
_PROSE_PIN = r'``([0-9][0-9.]*)`` in ``SKY_REMOTE_RAY_VERSION``'

# Files that legitimately pin a concrete Ray version, and the pattern that
# captures it. Every captured version must equal SKY_REMOTE_RAY_VERSION.
_PINNED_VERSION_FILES = [
    ('Dockerfile_k8s', _PIP_PIN),
    ('Dockerfile_k8s_gpu', _PIP_PIN),
    ('sky/catalog/images/provisioners/skypilot.sh', _SH_PIN),
    ('docs/source/reference/kubernetes/kubernetes-getting-started.rst',
     _PIP_PIN),
    ('docs/source/reference/architecture/internals.rst', _PROSE_PIN),
]


def _read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


@pytest.mark.parametrize('rel_path,pattern', _PINNED_VERSION_FILES)
def test_pinned_ray_version_matches_constant(rel_path, pattern):
    path = os.path.join(ROOT_DIR, *rel_path.split('/'))
    if not os.path.exists(path):
        pytest.skip(f'{rel_path} not present in this checkout')
    found = re.findall(pattern, _read(path))
    assert found, (f'{rel_path}: no Ray version pin matched {pattern!r}; '
                   'update the pattern if the file changed shape')
    for version in found:
        assert version == constants.SKY_REMOTE_RAY_VERSION, (
            f'{rel_path} pins ray {version} but SKY_REMOTE_RAY_VERSION is '
            f'{constants.SKY_REMOTE_RAY_VERSION}')


def test_templates_do_not_hardcode_ray_version():
    """Templates must use {{ray_version}} so a bump cannot leave them behind.

    Matches any version literal near a `ray` mention rather than only the
    current version, so the check fires whichever side goes stale.
    """
    templates_dir = os.path.join(ROOT_DIR, 'sky', 'templates')
    ray_line = re.compile(r'\bray\b', re.IGNORECASE)
    version_literal = re.compile(r'\b\d+\.\d+\.\d+\b')
    offenders = []
    for name in sorted(os.listdir(templates_dir)):
        if not name.endswith('.j2'):
            continue
        for lineno, line in enumerate(
                _read(os.path.join(templates_dir, name)).splitlines(), 1):
            if '{{ray_version}}' in line:
                continue
            if ray_line.search(line) and version_literal.search(line):
                offenders.append(f'{name}:{lineno}: {line.strip()}')
    assert not offenders, (
        'templates hardcode a Ray version; use {{ray_version}} instead so it '
        'cannot drift from SKY_REMOTE_RAY_VERSION:\n' + '\n'.join(offenders))


def test_ray_patch_files_are_paired_and_current():
    """Each patched module needs a .patch, a .diff, and a current header."""
    patches = {
        name[:-len('.py.patch')]
        for name in os.listdir(RAY_PATCHES_DIR)
        if name.endswith('.py.patch')
    }
    diffs = {
        name[:-len('.py.diff')]
        for name in os.listdir(RAY_PATCHES_DIR)
        if name.endswith('.py.diff')
    }
    assert patches == diffs, (
        f'.patch and .diff are out of sync: only .patch={patches - diffs}, '
        f'only .diff={diffs - patches}. Both are applied depending on whether '
        'the image ships the `patch` binary, so they must be regenerated '
        'together.')
    assert patches, 'no Ray patches found'

    for module in sorted(patches):
        for suffix in ('.py.patch', '.py.diff'):
            text = _read(os.path.join(RAY_PATCHES_DIR, module + suffix))
            expected = f'/ray-{constants.SKY_REMOTE_RAY_VERSION}/'
            assert expected in text, (
                f'{module}{suffix} does not reference ray '
                f'{constants.SKY_REMOTE_RAY_VERSION}; regenerate it against '
                'the pinned Ray version')
