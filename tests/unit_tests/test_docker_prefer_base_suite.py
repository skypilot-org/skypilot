"""Tests for the base-suite fallback in the VM docker container setup.

`DockerInitializer._setup_container` installs SkyPilot's prerequisites with
apt. When a release's companion suite (<codename>-security, <codename>-updates)
still serves an index but its pool has been pruned, `apt-get update` succeeds
and the install then 404s on every file it resolved to that suite. The shell in
`sky.provision.docker_utils._PREFER_BASE_SUITE_FN` detects exactly that case
and pins the base suite so a retry can succeed.

These tests execute that shell -- the constant the module actually ships, with
only its hardcoded absolute paths redirected into a temp dir -- so they assert
on the code that runs, not a copy of it. No container is required.
"""
import os
import subprocess
import tempfile
import textwrap

import pytest

from sky.provision import docker_utils

# What apt prints when a companion suite's pool has been pruned but its index
# is still served: the fetch of the newer version fails, nothing is installed.
_FETCH_FAILURE = textwrap.dedent("""\
    Err:1 http://deb.debian.org/debian-security bullseye-security/main amd64 curl amd64 7.74.0-1.3+deb11u16
      404  Not Found [IP: 151.101.2.132 80]
    E: Failed to fetch http://deb.debian.org/debian-security/pool/updates/main/c/curl/curl_7.74.0-1.3+deb11u16_amd64.deb  404  Not Found [IP: 151.101.2.132 80]
    E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?
    """)
_DPKG_FAILURE = 'E: Sub-process /usr/bin/dpkg returned an error code (1)\n'
# A transient failure prints the same "Failed to fetch" text but no 404; it
# must not pin anything, or a blip would cost the node its security suite.
_TRANSIENT_FAILURE = textwrap.dedent("""\
    Err:1 http://deb.debian.org/debian-security bullseye-security/main amd64 curl amd64 7.74.0-1.3+deb11u16
      Could not resolve 'deb.debian.org'
    E: Failed to fetch http://deb.debian.org/debian-security/pool/updates/main/c/curl/curl_7.74.0-1.3+deb11u16_amd64.deb  Could not resolve 'deb.debian.org'
    """)
# A 404 from a third-party repository, on the base suite: not a companion
# suite of the release, so preferring the base suite would not help.
_THIRD_PARTY_404 = textwrap.dedent("""\
    Err:1 https://download.docker.com/linux/debian bullseye/stable amd64 docker-ce amd64 5:24.0.7-1~debian.11~bullseye
      404  Not Found [IP: 13.224.29.98 443]
    E: Failed to fetch https://download.docker.com/linux/debian/dists/bullseye/pool/stable/amd64/docker-ce_24.0.7-1~debian.11~bullseye_amd64.deb  404  Not Found [IP: 13.224.29.98 443]
    """)

_OS_RELEASE_BULLSEYE = 'ID=debian\nVERSION_ID="11"\nVERSION_CODENAME=bullseye\n'
_OS_RELEASE_NO_CODENAME = 'ID=someotherlinux\nVERSION_ID="1"\n'

_POLICY_WITH_BULLSEYE = textwrap.dedent("""\
     500 http://deb.debian.org/debian-security bullseye-security/main amd64 Packages
         release v=11,o=Debian,a=oldoldstable-security,n=bullseye-security,l=Debian-Security,c=main,b=amd64
     500 http://deb.debian.org/debian bullseye/main amd64 Packages
         release v=11,o=Debian,a=oldoldstable,n=bullseye,l=Debian,c=main,b=amd64
    """)
# Only the companion suite: apt has no index for the codename itself, so the
# preference would leave nothing installable.
_POLICY_WITHOUT_BULLSEYE = textwrap.dedent("""\
     500 http://deb.debian.org/debian-security bullseye-security/main amd64 Packages
         release v=11,o=Debian,a=oldoldstable-security,n=bullseye-security,l=Debian-Security,c=main,b=amd64
    """)


def _harness(tmp: str,
             attempt_log: str,
             policy: str = _POLICY_WITH_BULLSEYE,
             os_release: str = _OS_RELEASE_BULLSEYE,
             conf: str = None) -> str:
    """Builds a script that calls the real helper against stubbed surroundings.

    The helper is verbatim from the module; only /etc/os-release and the apt
    preference path are redirected into ``tmp``, and ``apt-cache`` is stubbed
    on PATH so the test does not depend on the host's apt state.
    """
    conf = conf or os.path.join(tmp, 'default-release.conf')
    body = docker_utils._PREFER_BASE_SUITE_FN.replace(  # pylint: disable=protected-access
        '/etc/os-release', os.path.join(tmp, 'os-release')).replace(
            '/etc/apt/apt.conf.d/99-skypilot-default-release', conf)

    bin_dir = os.path.join(tmp, 'bin')
    os.makedirs(bin_dir, exist_ok=True)
    apt_cache = os.path.join(bin_dir, 'apt-cache')
    with open(apt_cache, 'w', encoding='utf-8') as f:
        f.write('#!/usr/bin/env bash\ncat <<\'POLICY\'\n' + policy + 'POLICY\n')
    os.chmod(apt_cache, 0o755)

    log = os.path.join(tmp, 'attempt.log')
    with open(log, 'w', encoding='utf-8') as f:
        f.write(attempt_log)
    with open(os.path.join(tmp, 'os-release'), 'w', encoding='utf-8') as f:
        f.write(os_release)

    return (f'export PATH={bin_dir}:$PATH\n' + body + '\n' +
            f'sky_prefer_base_suite {log}\n')


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(['bash', '-c', script],
                          capture_output=True,
                          text=True,
                          check=False)


def _read(path: str) -> str:
    if not os.path.exists(path):
        return ''
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def test_companion_suite_404_pins_the_base_suite():
    """The case this exists for: a 404 from <codename>-security."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = os.path.join(tmp, 'default-release.conf')
        result = _run(_harness(tmp, _FETCH_FAILURE))
        assert result.returncode == 0, result.stderr[-800:]
        assert _read(conf) == 'APT::Default-Release "bullseye";\n'
        assert 'preferring the base suite bullseye' in result.stdout


@pytest.mark.parametrize(('name', 'attempt_log'), [
    ('dpkg error', _DPKG_FAILURE),
    ('transient fetch failure with no 404', _TRANSIENT_FAILURE),
    ('404 from a third-party repo on the base suite', _THIRD_PARTY_404),
])
def test_other_failures_do_not_pin_anything(name, attempt_log):
    """Only a companion-suite 404 may cost the node its security suite."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = os.path.join(tmp, 'default-release.conf')
        result = _run(_harness(tmp, attempt_log))
        assert result.returncode != 0, f'{name}: unexpectedly pinned'
        assert not os.path.exists(conf), name


def test_no_index_for_the_codename_falls_through():
    """A derived distro whose base suite apt cannot see is left alone."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = os.path.join(tmp, 'default-release.conf')
        result = _run(
            _harness(tmp, _FETCH_FAILURE, policy=_POLICY_WITHOUT_BULLSEYE))
        assert result.returncode != 0
        assert not os.path.exists(conf)


def test_missing_version_codename_falls_through():
    with tempfile.TemporaryDirectory() as tmp:
        conf = os.path.join(tmp, 'default-release.conf')
        result = _run(
            _harness(tmp, _FETCH_FAILURE, os_release=_OS_RELEASE_NO_CODENAME))
        assert result.returncode != 0
        assert not os.path.exists(conf)


def test_already_pinned_does_not_retry_again():
    """Once the preference is in place a further failure is a real failure;
    returning 0 here would buy a pointless identical retry."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = os.path.join(tmp, 'default-release.conf')
        with open(conf, 'w', encoding='utf-8') as f:
            f.write('APT::Default-Release "bullseye";\n')
        result = _run(_harness(tmp, _FETCH_FAILURE, conf=conf))
        assert result.returncode != 0


def test_unwritable_preference_falls_through():
    """If the preference cannot be written, the retry would be identical."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = os.path.join(tmp, 'no-such-dir', 'default-release.conf')
        result = _run(_harness(tmp, _FETCH_FAILURE, conf=conf))
        assert result.returncode != 0
        assert not os.path.exists(conf)


def test_spliced_shell_has_no_single_quote():
    """The setup command nests this shell inside `bash -lc '...'`, so a single
    quote anywhere in it would end the command early and change what runs."""
    assert "'" not in docker_utils._PREFER_BASE_SUITE_FN  # pylint: disable=protected-access
    assert "'" not in docker_utils._APT_INSTALL_DEPS_CMD  # pylint: disable=protected-access
