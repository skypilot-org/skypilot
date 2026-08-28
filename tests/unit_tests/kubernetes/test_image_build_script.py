"""Tests for sky/catalog/images/skypilot-k8s-image.sh.

The script picks the base image and the version tag for the Kubernetes images.
Getting either wrong is silent: a build that forgot `-b` used to produce a
correctly-named tag on a stale base, and a hardcoded suffix would name a base
the image was not built on. Run the script with a stub `docker` on PATH and
assert what it resolved.
"""
import os
import pathlib
import re
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / 'sky/catalog/images/skypilot-k8s-image.sh'
DOCKERFILE_CPU = REPO_ROOT / 'Dockerfile_k8s'
DOCKERFILE_GPU = REPO_ROOT / 'Dockerfile_k8s_gpu'

# The tag the GPU labeler job and `sky local up` pull by name.
_SHARED_TAG = 'latest'
_DATE_TAG = r'\d{12}'

_DOCKER_STUB = """#!/bin/sh
# Stub docker: `buildx inspect` succeeds so no builder is created; everything
# else is recorded and never run.
if [ "$1" = "buildx" ] && [ "$2" = "inspect" ]; then
    exit 0
fi
printf '%s\\n' "$*" >> "$DOCKER_LOG"
exit 0
"""


def _arg_base_image(dockerfile: pathlib.Path) -> str:
    for line in dockerfile.read_text().splitlines():
        if line.startswith('ARG BASE_IMAGE='):
            return line[len('ARG BASE_IMAGE='):]
    raise AssertionError(f'no ARG BASE_IMAGE in {dockerfile}')


class _Build:
    """What one script invocation resolved."""

    def __init__(self, stdout: str, docker_log: str):
        self.stdout = stdout
        self.docker_log = docker_log
        self.base = self._field('Base image')
        self.suffix = self._field('Tag suffix')
        self.image = self._field('Building image')
        self.warnings = [
            line for line in stdout.splitlines() if 'Warning:' in line
        ]

    def _field(self, name: str) -> str:
        match = re.search(f'^{name}: (.*)$', self.stdout, re.MULTILINE)
        assert match is not None, f'{name!r} missing from:\n{self.stdout}'
        return match.group(1)

    @property
    def tag(self) -> str:
        """The image reference without its registry path, e.g. skypilot:tag."""
        return self.image.rsplit('/', 1)[-1]

    @property
    def build_arg_base(self):
        """BASE_IMAGE passed to buildx, or None when the Dockerfile default is
        left to apply."""
        match = re.search(r'--build-arg BASE_IMAGE=(\S+)', self.docker_log)
        return match.group(1) if match is not None else None


@pytest.fixture(name='run_build')
def run_build_fixture(tmp_path):

    def run(*flags: str) -> _Build:
        stub_dir = tmp_path / 'bin'
        stub_dir.mkdir(exist_ok=True)
        stub = stub_dir / 'docker'
        stub.write_text(_DOCKER_STUB)
        stub.chmod(0o755)
        docker_log = tmp_path / 'docker.log'
        docker_log.write_text('')

        env = dict(os.environ)
        env['PATH'] = f'{stub_dir}{os.pathsep}{env["PATH"]}'
        env['DOCKER_LOG'] = str(docker_log)
        proc = subprocess.run(['bash', str(SCRIPT), *flags],
                              cwd=REPO_ROOT,
                              env=env,
                              capture_output=True,
                              text=True,
                              check=True)
        return _Build(proc.stdout, docker_log.read_text())

    return run


class TestBaseImage:

    def test_default_tracks_the_dockerfile(self, run_build):
        """A plain build must use the base we ship, not an older one."""
        assert run_build('-p').base == _arg_base_image(DOCKERFILE_CPU)
        assert run_build('-p', '-g').base == _arg_base_image(DOCKERFILE_GPU)

    def test_default_passes_no_build_arg(self, run_build):
        """The Dockerfile default applies, so nothing to override."""
        assert run_build('-p').build_arg_base is None

    def test_dash_b_overrides(self, run_build):
        build = run_build('-p', '-b', 'ubuntu:26.04')
        assert build.base == 'ubuntu:26.04'
        assert build.build_arg_base == 'ubuntu:26.04'

    def test_shipped_bases_are_the_same_ubuntu_release(self):
        """nvidia/cuda:*-ubuntuXXXX is layered on ubuntu:XXXX; a mismatch means
        the CPU and GPU images ship different libc."""
        cpu = re.search(r'ubuntu[:-]?(\d\d)\.?(\d\d)',
                        _arg_base_image(DOCKERFILE_CPU))
        gpu = re.search(r'ubuntu[:-]?(\d\d)\.?(\d\d)',
                        _arg_base_image(DOCKERFILE_GPU))
        assert cpu is not None and gpu is not None
        assert cpu.groups() == gpu.groups()


class TestTagSuffix:

    def test_derived_from_the_default_base(self, run_build):
        release = re.search(r'ubuntu[:-]?(\d\d)\.?(\d\d)',
                            _arg_base_image(DOCKERFILE_CPU))
        assert release is not None
        expected = f'ubuntu{release.group(1)}{release.group(2)}'
        build = run_build('-p')
        assert build.suffix == expected
        assert re.fullmatch(f'skypilot:{_DATE_TAG}-{expected}', build.tag)

    def test_derived_from_an_overridden_base(self, run_build):
        """The suffix must follow -b, or a variant tag names the wrong base."""
        assert run_build('-p', '-b', 'ubuntu:26.04').suffix == 'ubuntu2604'
        gpu = run_build('-p', '-g', '-b',
                        'nvidia/cuda:12.8.1-runtime-ubuntu26.04')
        assert gpu.suffix == 'ubuntu2604'
        assert re.fullmatch(f'skypilot-gpu:{_DATE_TAG}-ubuntu2604', gpu.tag)

    def test_explicit_suffix_wins(self, run_build):
        build = run_build('-p', '-s', 'custom')
        assert build.suffix == 'custom'
        assert re.fullmatch(f'skypilot:{_DATE_TAG}-custom', build.tag)

    def test_unrecognized_base_warns_and_drops_the_suffix(self, run_build):
        build = run_build('-p', '-b', 'alpine:3.20')
        assert build.suffix == '<none>'
        assert re.fullmatch(f'skypilot:{_DATE_TAG}', build.tag)
        assert len(build.warnings) == 1


class TestLatestTag:

    def test_stays_unsuffixed(self, run_build):
        """Consumers pull `latest` by name, so the derived suffix is dropped."""
        assert run_build('-p', '-l').tag == f'skypilot:{_SHARED_TAG}'
        assert run_build('-p', '-g', '-l').tag == f'skypilot-gpu:{_SHARED_TAG}'

    def test_explicit_suffix_still_applies(self, run_build):
        build = run_build('-p', '-l', '-s', 'custom')
        assert build.tag == f'skypilot:{_SHARED_TAG}-custom'

    def test_variant_base_warns(self, run_build):
        """-l -b would overwrite the shared tag with a variant build."""
        build = run_build('-p', '-l', '-b', 'ubuntu:26.04')
        assert build.tag == f'skypilot:{_SHARED_TAG}'
        assert len(build.warnings) == 1

    def test_variant_base_with_explicit_suffix_is_quiet(self, run_build):
        build = run_build('-p', '-l', '-b', 'ubuntu:26.04', '-s', 'ubuntu2604')
        assert build.tag == f'skypilot:{_SHARED_TAG}-ubuntu2604'
        assert not build.warnings


class TestPassthrough:

    @pytest.mark.parametrize('region', ['us', 'europe', 'asia'])
    def test_region_selects_the_registry_host(self, run_build, region):
        build = run_build('-p', '-r', region)
        assert build.image.startswith(
            f'{region}-docker.pkg.dev/sky-dev-465/skypilotk8s/')

    def test_gpu_selects_the_gpu_dockerfile(self, run_build):
        assert '-f Dockerfile_k8s_gpu' in run_build('-p', '-g').docker_log
        assert '-f Dockerfile_k8s ' in run_build('-p').docker_log

    def test_push_builds_both_architectures(self, run_build):
        assert ('--platform linux/amd64,linux/arm64'
                in run_build('-p').docker_log)
