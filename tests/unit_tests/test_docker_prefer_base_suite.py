"""Tests for the base-suite fallback in the VM docker container setup.

`DockerInitializer._setup_container` installs SkyPilot's prerequisites with
apt. When a release's companion suite (<codename>-security, <codename>-updates)
still serves an index but its pool has been pruned, `apt-get update` succeeds
and the install then 404s on every file it resolved to that suite. Two shells
in `sky.provision.docker_utils` react to that: `_PREFER_BASE_SUITE_FN`
deprioritizes the companion suites, and `_FORCE_BASE_SUITE_FN` -- only if that
was not enough -- makes the base suite authoritative and allows downgrades.

These tests execute those shells -- the constants the module actually ships,
with only their hardcoded absolute paths redirected into a temp dir -- so they
assert on the code that runs, not a copy of it. No container is required.
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
# What the deprioritized retry prints when the base suite alone cannot satisfy
# the install: a package it must install depends on the exact version of one
# already installed from the pruned suite, which only a downgrade can meet.
_UNSATISFIABLE = textwrap.dedent("""\
    The following packages have unmet dependencies:
     openssh-server : Depends: openssh-client (= 1:8.4p1-5+deb11u3) but 1:8.4p1-5+deb11u5 is to be installed
    E: Unable to correct problems, you have held broken packages.
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
# Ubuntu shares one Codename across its pockets and tells them apart by Suite,
# so there is no n=<codename>-<pocket> entry to key the base pin off.
_POLICY_UBUNTU = textwrap.dedent("""\
     500 http://archive.ubuntu.com/ubuntu jammy-security/main amd64 Packages
         release v=22.04,o=Ubuntu,a=jammy-security,n=jammy,l=Ubuntu,c=main,b=amd64
     500 http://archive.ubuntu.com/ubuntu jammy/main amd64 Packages
         release v=22.04,o=Ubuntu,a=jammy,n=jammy,l=Ubuntu,c=main,b=amd64
    """)

_COMPANIONS_DEPRIORITIZED = textwrap.dedent("""\
    Package: *
    Pin: release n=bullseye-security
    Pin-Priority: 1

    Package: *
    Pin: release n=bullseye-updates
    Pin-Priority: 1

    Package: *
    Pin: release a=bullseye-security
    Pin-Priority: 1

    Package: *
    Pin: release a=bullseye-updates
    Pin-Priority: 1

    """)


def _harness(tmp: str,
             call: str,
             attempt_log: str,
             policy: str = _POLICY_WITH_BULLSEYE,
             os_release: str = _OS_RELEASE_BULLSEYE,
             pref: str = None,
             conf: str = None) -> str:
    """Builds a script that calls the real helpers against stubbed surroundings.

    The helpers are verbatim from the module; only /etc/os-release and the two
    apt paths are redirected into ``tmp``, and ``apt-cache`` is stubbed on PATH
    so the test does not depend on the host's apt state.
    """
    pref = pref or os.path.join(tmp, 'preferences')
    conf = conf or os.path.join(tmp, 'apt.conf')
    # pylint: disable=protected-access
    body = (docker_utils._PREFER_BASE_SUITE_FN +
            docker_utils._FORCE_BASE_SUITE_FN).replace(
                '/etc/os-release', os.path.join(tmp, 'os-release')).replace(
                    docker_utils._APT_PREFERENCES_PATH,
                    pref).replace(docker_utils._APT_CONF_PATH, conf)

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

    return f'export PATH={bin_dir}:$PATH\n' + body + '\n' + f'{call} {log}\n'


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


def test_companion_suite_404_deprioritizes_the_companions():
    """The case this exists for: a 404 from <codename>-security.

    The companions must land *below* the priority 100 apt gives an installed
    version. Raising the base suite instead would be a no-op for any package
    the image ships at a version newer than the base suite's -- apt would go
    right back to the companion version that 404s.
    """
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, 'preferences')
        result = _run(
            _harness(tmp, 'sky_prefer_base_suite', _FETCH_FAILURE, pref=pref))
        assert result.returncode == 0, result.stderr[-800:]
        assert _read(pref) == _COMPANIONS_DEPRIORITIZED
        assert 'deprioritizing the companion suites of bullseye' in result.stdout


@pytest.mark.parametrize(('name', 'attempt_log'), [
    ('dpkg error', _DPKG_FAILURE),
    ('transient fetch failure with no 404', _TRANSIENT_FAILURE),
    ('404 from a third-party repo on the base suite', _THIRD_PARTY_404),
])
def test_other_failures_do_not_pin_anything(name, attempt_log):
    """Only a companion-suite 404 may cost the node its security suite."""
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, 'preferences')
        result = _run(
            _harness(tmp, 'sky_prefer_base_suite', attempt_log, pref=pref))
        assert result.returncode != 0, f'{name}: unexpectedly pinned'
        assert not os.path.exists(pref), name


def test_no_index_for_the_codename_falls_through():
    """A derived distro whose base suite apt cannot see is left alone."""
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, 'preferences')
        result = _run(
            _harness(tmp,
                     'sky_prefer_base_suite',
                     _FETCH_FAILURE,
                     policy=_POLICY_WITHOUT_BULLSEYE,
                     pref=pref))
        assert result.returncode != 0
        assert not os.path.exists(pref)


def test_missing_version_codename_falls_through():
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, 'preferences')
        result = _run(
            _harness(tmp,
                     'sky_prefer_base_suite',
                     _FETCH_FAILURE,
                     os_release=_OS_RELEASE_NO_CODENAME,
                     pref=pref))
        assert result.returncode != 0
        assert not os.path.exists(pref)


def test_already_deprioritized_does_not_retry_again():
    """Once the preference is in place, step one has nothing left to change;
    returning 0 here would buy a pointless identical retry."""
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, 'preferences')
        with open(pref, 'w', encoding='utf-8') as f:
            f.write(_COMPANIONS_DEPRIORITIZED)
        result = _run(
            _harness(tmp, 'sky_prefer_base_suite', _FETCH_FAILURE, pref=pref))
        assert result.returncode != 0


def test_unwritable_preference_falls_through():
    """If the preference cannot be written, the retry would be identical."""
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, 'no-such-dir', 'preferences')
        result = _run(
            _harness(tmp, 'sky_prefer_base_suite', _FETCH_FAILURE, pref=pref))
        assert result.returncode != 0
        assert not os.path.exists(pref)


def test_force_base_suite_after_an_unsatisfiable_retry():
    """Step two: the base suite alone could not satisfy the install, so make it
    authoritative and let apt move packages backwards to reach it."""
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, 'preferences')
        conf = os.path.join(tmp, 'apt.conf')
        with open(pref, 'w', encoding='utf-8') as f:
            f.write(_COMPANIONS_DEPRIORITIZED)
        result = _run(
            _harness(tmp,
                     'sky_force_base_suite',
                     _UNSATISFIABLE,
                     pref=pref,
                     conf=conf))
        assert result.returncode == 0, result.stderr[-800:]
        # Debian: the companions differ from the base in Codename, so the base
        # pin can key off n= without also matching them.
        assert _read(pref).endswith(
            'Package: *\nPin: release n=bullseye\nPin-Priority: 1001\n\n')
        assert _read(conf) == 'APT::Get::allow-downgrades "true";\n'
        assert 'making it authoritative' in result.stdout


def test_force_base_suite_keys_off_suite_on_ubuntu():
    """Ubuntu's pockets all share n=<codename>; keying the base pin off n=
    there would raise the companions back to 1001 too."""
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, 'preferences')
        os_release = ('ID=ubuntu\nVERSION_ID="22.04"\n'
                      'VERSION_CODENAME=jammy\n')
        with open(pref, 'w', encoding='utf-8') as f:
            f.write('Package: *\nPin: release a=jammy-security\n'
                    'Pin-Priority: 1\n\n')
        result = _run(
            _harness(tmp,
                     'sky_force_base_suite',
                     _UNSATISFIABLE,
                     policy=_POLICY_UBUNTU,
                     os_release=os_release,
                     pref=pref))
        assert result.returncode == 0, result.stderr[-800:]
        assert _read(pref).endswith(
            'Package: *\nPin: release a=jammy\nPin-Priority: 1001\n\n')


@pytest.mark.parametrize(('name', 'attempt_log'), [
    ('a dpkg error is not something a downgrade can fix', _DPKG_FAILURE),
    ('nor is a transient fetch failure', _TRANSIENT_FAILURE),
])
def test_force_base_suite_only_for_what_it_can_fix(name, attempt_log):
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, 'preferences')
        conf = os.path.join(tmp, 'apt.conf')
        with open(pref, 'w', encoding='utf-8') as f:
            f.write(_COMPANIONS_DEPRIORITIZED)
        result = _run(
            _harness(tmp,
                     'sky_force_base_suite',
                     attempt_log,
                     pref=pref,
                     conf=conf))
        assert result.returncode != 0, name
        assert not os.path.exists(conf), name
        assert _read(pref) == _COMPANIONS_DEPRIORITIZED, name


def test_force_base_suite_never_runs_on_its_own():
    """Downgrading is only ever justified once step one has established that
    this release's companion suites are unusable."""
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, 'preferences')
        conf = os.path.join(tmp, 'apt.conf')
        result = _run(
            _harness(tmp,
                     'sky_force_base_suite',
                     _UNSATISFIABLE,
                     pref=pref,
                     conf=conf))
        assert result.returncode != 0
        assert not os.path.exists(pref)
        assert not os.path.exists(conf)


def test_force_base_suite_is_applied_at_most_once():
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, 'preferences')
        conf = os.path.join(tmp, 'apt.conf')
        with open(pref, 'w', encoding='utf-8') as f:
            f.write(_COMPANIONS_DEPRIORITIZED)
        with open(conf, 'w', encoding='utf-8') as f:
            f.write('APT::Get::allow-downgrades "true";\n')
        result = _run(
            _harness(tmp,
                     'sky_force_base_suite',
                     _UNSATISFIABLE,
                     pref=pref,
                     conf=conf))
        assert result.returncode != 0
        assert _read(pref) == _COMPANIONS_DEPRIORITIZED


def test_spliced_shell_has_no_single_quote():
    """The setup command nests these shells inside `bash -lc '...'`, so a
    single quote anywhere in them would end the command early and change what
    runs."""
    # pylint: disable=protected-access
    assert "'" not in docker_utils._PREFER_BASE_SUITE_FN
    assert "'" not in docker_utils._FORCE_BASE_SUITE_FN
    assert "'" not in docker_utils._APT_INSTALL_DEPS_CMD
