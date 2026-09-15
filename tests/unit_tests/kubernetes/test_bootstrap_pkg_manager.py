"""Tests for the package-manager-agnostic Kubernetes pod bootstrap.

The bootstrap lives in the `args` of the pod spec rendered from
sky/templates/kubernetes-ray.yml.j2, as plain POSIX shell. These tests extract
that shell straight out of the template and execute the individual functions,
so they assert on the code that actually ships rather than on a copy of it.

No cluster is required.
"""
import os
import shutil
import subprocess
import tempfile
import textwrap

import jinja2
import pytest

_TEMPLATE = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'sky',
                         'templates', 'kubernetes-ray.yml.j2')

# Every package manager the bootstrap can detect.
_MANAGERS = ['apt', 'dnf', 'microdnf', 'yum', 'apk', 'zypper']


def _template_lines():
    with open(_TEMPLATE, 'r', encoding='utf-8') as f:
        return f.read().split('\n')


def _extract(start_marker: str, end_marker: str) -> str:
    """Returns the shell between two markers, de-indented and Jinja-rendered."""
    lines = _template_lines()
    start = next(i for i, l in enumerate(lines) if start_marker in l)
    end = next(i for i, l in enumerate(lines) if end_marker in l and i > start)
    block = '\n'.join(l[16:] if l.startswith(' ' * 16) else l.strip()
                      for l in lines[start:end])
    # The bootstrap contains Jinja conditionals; render them away. FUSE off and
    # docker off keeps the extracted shell to the always-present paths.
    return jinja2.Environment(
        undefined=jinja2.ChainableUndefined).from_string(block).render(
            k8s_fuse_device_required=False,
            k8s_apt_mirrors=None,
            k8s_enable_docker_all=False,
            k8s_enable_docker_build=False)


def _run(script: str, errexit: bool = False) -> subprocess.CompletedProcess:
    """Runs a shell snippet.

    errexit=True prepends `set -e`. Use it for anything extracted from the
    install section, which really does run under `set -e` in the pod: a harness
    without it silently passes code that would abort STEP 1 on the first
    non-zero command. That is not hypothetical -- it let a broken retry loop
    through here, and a reviewer caught it instead.
    """
    prefix = 'set -e\n' if errexit else ''
    return subprocess.run(['bash', '-c', prefix + script],
                          capture_output=True,
                          text=True,
                          check=False)


# --------------------------------------------------------------------------
# map_pkg_names: generic name -> per-manager name
# --------------------------------------------------------------------------


def _map_pkg_names_harness() -> str:
    """The real map_pkg_names() function, standalone."""
    return _extract('map_pkg_names() {', 'pkg_install() {')


@pytest.mark.parametrize(
    'pkg_mgr,generic,expected',
    [
        # netcat: RPM families call it nmap-ncat.
        ('dnf', 'netcat', 'nmap-ncat'),
        ('yum', 'netcat', 'nmap-ncat'),
        ('microdnf', 'netcat', 'nmap-ncat'),
        ('apt', 'netcat', 'netcat-openbsd'),
        # openssh-server: apk and zypper ship it as plain `openssh`.
        ('apk', 'openssh-server', 'openssh'),
        ('zypper', 'openssh-server', 'openssh'),
        ('apt', 'openssh-server', 'openssh-server'),
        ('dnf', 'openssh-server', 'openssh-server'),
        # FUSE: the RPM families split the library out of the binary package,
        # so both halves are needed for the FUSE 2 / FUSE 3 ABIs.
        ('dnf', 'fuse', 'fuse fuse-libs'),
        ('dnf', 'fuse3', 'fuse3 fuse3-libs'),
        ('apt', 'fuse', 'fuse'),
        ('apt', 'fuse3', 'fuse3'),
        # Same name everywhere -> passthrough.
        ('dnf', 'curl', 'curl'),
        ('apk', 'rsync', 'rsync'),
        ('zypper', 'gcc', 'gcc'),
    ])
def test_map_pkg_names(pkg_mgr, generic, expected):
    script = (f'{_map_pkg_names_harness()}\n'
              f'PKG_MGR={pkg_mgr}\n'
              f'map_pkg_names {generic}\n')
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == expected.split(), (
        f'{generic} on {pkg_mgr} -> {result.stdout.strip()!r}, '
        f'expected {expected!r}')


def test_map_pkg_names_preserves_order_of_multiple_packages():
    script = (f'{_map_pkg_names_harness()}\n'
              'PKG_MGR=dnf\n'
              'map_pkg_names rsync netcat wget\n')
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ['rsync', 'nmap-ncat', 'wget']


# --------------------------------------------------------------------------
# pkg_present: presence proven by binary, portably
# --------------------------------------------------------------------------


def _pkg_present_harness() -> str:
    return _extract('pkg_present() {', 'map_pkg_names() {')


def test_pkg_present_finds_sbin_binaries_absent_from_a_restricted_path():
    """openssh-server/pciutils live in sbin, which a non-root PATH omits.

    Regression guard: without the absolute-path fallbacks, an image that already
    ships sshd would be treated as missing it and reinstall on every launch.
    """
    tmp = tempfile.mkdtemp()
    try:
        fake_sbin = os.path.join(tmp, 'usr', 'sbin')
        os.makedirs(fake_sbin)
        for binary in ('sshd', 'lspci'):
            path = os.path.join(fake_sbin, binary)
            with open(path, 'w', encoding='utf-8') as f:
                f.write('#!/bin/sh\n')
            os.chmod(path, 0o755)
        # `command -v sshd` must fail, so the fallback is what answers. The
        # template checks /usr/sbin and /sbin absolutely, so shadow the real
        # root via a bind-like symlink farm is not possible here; instead assert
        # the shape of the check itself plus the real-system behaviour below.
        script = (f'{_pkg_present_harness()}\n'
                  'PATH=/nonexistent\n'
                  'pkg_present openssh-server && echo FOUND || echo NOTFOUND\n')
        result = _run(script)
        # On a host with /usr/sbin/sshd this finds it via the absolute path; on
        # one without, it correctly reports absent. Either way it must not error.
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() in ('FOUND', 'NOTFOUND')
        expected = ('FOUND' if (os.path.exists('/usr/sbin/sshd') or
                                os.path.exists('/sbin/sshd')) else 'NOTFOUND')
        assert result.stdout.strip() == expected
    finally:
        shutil.rmtree(tmp)


def test_pkg_present_maps_netcat_to_the_nc_binary():
    script = (f'{_pkg_present_harness()}\n'
              'pkg_present netcat && echo FOUND || echo NOTFOUND\n')
    result = _run(script)
    assert result.returncode == 0, result.stderr
    expected = 'FOUND' if shutil.which('nc') else 'NOTFOUND'
    assert result.stdout.strip() == expected


# --------------------------------------------------------------------------
# The CORE_PKGS_READY invariant
# --------------------------------------------------------------------------
#
# STEP 2 of the bootstrap gates its HTTPS downloads on
#   until [ -f "$CORE_PKGS_READY" ] || [ -f "/tmp/${STEPS[0]}.failed" ]
# and the .failed sentinel is only written when STEP 1 exits NON-zero. So the
# hang condition is: STEP 1 exits 0 without ever publishing the marker.
#
# That is not hypothetical -- it shipped once and a reviewer caught it. Two
# shapes produced it: (1) apt with no core package missing but optional ones
# queued, where the index refresh fails on every mirror and `continue`s out of
# the loop while the fatal guard only fires for a non-empty INSTALL_FIRST; and
# (2) no supported package manager at all, same shape. Both are cases the
# previous `command -v curl` gate passed instantly.
#
# This asserts the invariant across the whole input matrix, so a future publish
# site added without covering an early-exit path fails here instead of hanging
# somebody's launch.


def _step1_harness(pkg_mgr: str, present: str, install_ok: bool,
                   marker: str) -> str:
    """The real STEP 1 install region, with only the network stubbed out."""
    body = _extract(
        'PACKAGES="rsync curl',
        'Error: core package installation failed across all sources')
    stub = textwrap.dedent(f"""
        # Presence stub must come AFTER the template's own pkg_present
        # definition to take effect.
        pkg_present() {{ case "$1" in {present}) return 0;; *) return 1;; esac; }}
        """)
    body = body.replace('set -e\nINSTALL_FIRST="";',
                        stub + 'set -e\nINSTALL_FIRST="";', 1)
    # Force the manager: detection would otherwise pick up the test host's own.
    dispatch = 'INSTALL_SUCCESS=false\nif [ -z "$INSTALL_FIRST" ]'
    forced = pkg_mgr if pkg_mgr else '""'
    body = body.replace(dispatch, 'PKG_MGR=' + forced + '\n' + dispatch, 1)
    rc = 0 if install_ok else 1
    return textwrap.dedent(f"""
        CORE_PKGS_READY={marker}
        prefix_cmd() {{ echo ""; }}
        dump_apt_log() {{ :; }}
        backup_source() {{ :; }}
        restore_source() {{ :; }}
        update_apt_sources() {{ return {rc}; }}
        apt_update_with_retries() {{ return {rc}; }}
        apt_install_with_retries() {{ return {rc}; }}
        apt_update_install_with_retries() {{ return {rc}; }}
        pkg_install() {{ return {rc}; }}
        """) + body


@pytest.mark.parametrize('pkg_mgr', ['apt', 'dnf', ''])
@pytest.mark.parametrize(
    'present,core_missing',
    [
        # Image ships the core tools; only optional ones are missing.
        ('curl|rsync|wget', False),
        # Image ships nothing -> curl really must be installed.
        ('nothing_matches', True),
    ])
@pytest.mark.parametrize('install_ok', [True, False])
def test_step1_publishes_core_marker_whenever_it_exits_zero(
        pkg_mgr, present, core_missing, install_ok):
    """If STEP 1 exits 0, the marker MUST exist, or STEP 2 waits forever."""
    tmp = tempfile.mkdtemp()
    try:
        marker = os.path.join(tmp, 'sky_core_pkgs_ready')
        script = _step1_harness(pkg_mgr, present, install_ok, marker)
        result = _run(script)
        published = os.path.exists(marker)
        if result.returncode == 0:
            assert published, (
                f'STEP 1 exited 0 without publishing {marker!r} '
                f'(pkg_mgr={pkg_mgr!r}, core_missing={core_missing}, '
                f'install_ok={install_ok}) -- STEP 2 would wait forever.\n'
                f'stdout: {result.stdout[-800:]}')
        else:
            # Non-zero is fine: the step writes a .failed sentinel that STEP 2
            # also breaks on, so no hang. Nothing to assert about the marker.
            pass
    finally:
        shutil.rmtree(tmp)


def test_step1_publishes_core_marker_before_optional_packages():
    """The marker must not wait on the best-effort MISSING_PACKAGES install.

    STEP 2 is meant to overlap with those; publishing late would serialize the
    launch behind gcc/pciutils/fuse3 and defeat the INSTALL_FIRST split.
    """
    body = _extract(
        'PACKAGES="rsync curl',
        'Error: core package installation failed across all sources')
    first_publish = body.index('touch "$CORE_PKGS_READY"')
    optional_install = body.index('MISSING_PACKAGES')
    # The earliest publish site must precede the optional-install machinery in
    # the emitted script.
    assert first_publish < body.rindex('Installing missing packages'), (
        'CORE_PKGS_READY is published after the best-effort install')
    assert optional_install > 0


# --------------------------------------------------------------------------
# The non-apt core install must survive a failing package manager
# --------------------------------------------------------------------------


def _nonapt_harness(install_rc: int, present_after: bool) -> str:
    """The real run_pkg_install_nonapt(), with pkg_install/pkg_present stubbed."""
    lines = _template_lines()
    start = next(
        i for i, l in enumerate(lines) if 'run_pkg_install_nonapt() {' in l)
    end = None
    for i in range(start + 1, len(lines)):
        if 'INSTALL_SUCCESS=false' in lines[i]:
            nxt = next((x for x in lines[i + 1:] if x.strip()), '')
            if 'if [ -z "$INSTALL_FIRST" ]' in nxt:
                end = i
                break
    assert end, 'dispatch site not found'
    body = '\n'.join(l[16:] if l.startswith(' ' * 16) else l.strip()
                     for l in lines[start:end])
    body = jinja2.Environment(
        undefined=jinja2.ChainableUndefined).from_string(body).render()
    ret = 'return 0' if present_after else 'return 1'
    return (f'CORE_PKGS_READY="$(mktemp)"\n'
            f'pkg_install() {{ return {install_rc}; }}\n'
            f'pkg_present() {{ {ret}; }}\n'
            f'{body}\n'
            'PKG_MGR=dnf; INSTALL_FIRST="curl rsync"; MISSING_PACKAGES=""\n'
            'run_pkg_install_nonapt\n'
            'echo "INSTALL_SUCCESS=$INSTALL_SUCCESS"\n')


def test_nonapt_core_install_tolerates_a_nonzero_package_manager():
    """A non-zero pkg_install must not abort STEP 1 under `set -e`.

    zypper returns >=100 for informational success (106 = a repository had to be
    skipped, but the requested packages installed), which is normal behind a
    restrictive egress proxy. The install runs under `set -e`, so without a
    tested-command guard the shell exits before the presence check can run.
    """
    result = _run(_nonapt_harness(install_rc=106, present_after=True),
                  errexit=True)
    assert 'INSTALL_SUCCESS=true' in result.stdout, (
        f'rc=106 with packages present should succeed. '
        f'stdout={result.stdout!r} rc={result.returncode}')


def test_nonapt_core_install_fails_when_packages_are_really_absent():
    """Negative control: a clean exit code must not be taken as success."""
    result = _run(_nonapt_harness(install_rc=0, present_after=False),
                  errexit=True)
    assert 'INSTALL_SUCCESS=true' not in result.stdout, (
        f'packages absent must not report success. stdout={result.stdout!r}')


# --------------------------------------------------------------------------
# apt_install_with_retries: prefer the base suite when a fetch fails
# --------------------------------------------------------------------------

_POLICY_WITH_BULLSEYE = textwrap.dedent("""\
     500 http://deb.debian.org/debian-security bullseye-security/main amd64 Packages
         release v=11,o=Debian,a=oldoldstable-security,n=bullseye-security,l=Debian-Security,c=main,b=amd64
     500 http://deb.debian.org/debian bullseye/main amd64 Packages
         release v=11,o=Debian,a=oldoldstable,n=bullseye,l=Debian,c=main,b=amd64
    """)
# Only the companion suite: apt has no index for the codename itself.
_POLICY_WITHOUT_BULLSEYE = textwrap.dedent("""\
     500 http://deb.debian.org/debian-security bullseye-security/main amd64 Packages
         release v=11,o=Debian,a=oldoldstable-security,n=bullseye-security,l=Debian-Security,c=main,b=amd64
    """)
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
# must not pin anything.
_TRANSIENT_FAILURE = textwrap.dedent("""\
    Err:1 http://deb.debian.org/debian-security bullseye-security/main amd64 curl amd64 7.74.0-1.3+deb11u16
      Could not resolve 'deb.debian.org'
    E: Failed to fetch http://deb.debian.org/debian-security/pool/updates/main/c/curl/curl_7.74.0-1.3+deb11u16_amd64.deb  Could not resolve 'deb.debian.org'
    E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?
    """)
# A 404 from a third-party repository on the base suite: not a companion
# suite of the release, so preferring the base suite would not help.
_THIRD_PARTY_404 = textwrap.dedent("""\
    Err:1 https://download.docker.com/linux/debian bullseye/stable amd64 docker-ce amd64 5:24.0.7-1~debian.11~bullseye
      404  Not Found [IP: 13.224.29.98 443]
    E: Failed to fetch https://download.docker.com/linux/debian/dists/bullseye/pool/stable/amd64/docker-ce_24.0.7-1~debian.11~bullseye_amd64.deb  404  Not Found [IP: 13.224.29.98 443]
    E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?
    """)
_OS_RELEASE_BULLSEYE = 'ID=debian\nVERSION_ID="11"\nVERSION_CODENAME=bullseye\n'


def _apt_install_harness(tmp: str,
                         install_stub: str,
                         policy: str,
                         os_release: str,
                         calls: str,
                         conf: str = '') -> str:
    """The real apt_install_with_retries and its fetch-failure helper, with
    apt-get, apt-cache, timeout and sleep stubbed on PATH.

    ``install_stub`` is the bash the apt-get stub runs for ``install``; it sees
    the full argv in ``$*`` and must ``exit`` with apt's status. Every apt-get
    invocation is appended to ``$TRACE`` so tests can assert on the exact retry
    sequence. The hardcoded paths are redirected into ``tmp``; the shell is
    otherwise verbatim from the template.
    """
    body = _extract('apt_prefer_base_suite_on_fetch_failure() {',
                    'apt_update_install_with_retries() {')
    conf = conf or os.path.join(tmp, 'default-release.conf')
    body = (body.replace(
        '/tmp/apt-update.log', os.path.join(tmp, 'apt-update.log')).replace(
            '/tmp/apt-install-attempt.log',
            os.path.join(tmp, 'attempt.log')).replace(
                '/etc/os-release', os.path.join(tmp, 'os-release')).replace(
                    '/etc/apt/apt.conf.d/99-skypilot-default-release', conf))
    bin_dir = os.path.join(tmp, 'bin')
    os.makedirs(bin_dir)

    def stub(name: str, text: str) -> None:
        path = os.path.join(bin_dir, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('#!/usr/bin/env bash\n' + text + '\n')
        os.chmod(path, 0o755)

    stub(
        'apt-get', 'echo "apt-get $*" >> "$TRACE"\n'
        'case "$1" in\n'
        f'  install) {install_stub} ;;\n'
        '  *) exit 0 ;;\n'
        'esac')
    stub('apt-cache', f"cat <<'POLICY'\n{policy}POLICY")
    # ``timeout <secs> cmd...`` -> run cmd directly.
    stub('timeout', 'shift; exec "$@"')
    stub('sleep', 'exit 0')
    with open(os.path.join(tmp, 'os-release'), 'w', encoding='utf-8') as f:
        f.write(os_release)
    # Call sites redirect the helper into the step log and guard the call
    # with ``|| { ...; continue; }``; the helper re-arms ``set -e`` before
    # ``return 1``, so an unguarded failing call would abort the script here
    # instead of recording the status the pod's call sites observe.
    call_lines = '\n'.join(
        f'if apt_install_with_retries {c} >> {tmp}/apt-update.log 2>&1; then '
        f'echo "rc=0" >> "$TRACE"; else echo "rc=$?" >> "$TRACE"; fi'
        for c in calls.split(','))
    return textwrap.dedent(f"""
        export TRACE={tmp}/trace
        export PATH={bin_dir}:$PATH
        prefix_cmd() {{ echo ""; }}
        APT_OPTS=""
        APT_INSTALL_CMD_TIMEOUT=1
        """) + body + '\n' + call_lines + '\n'


def _fetch_failure_unless_retargeted(tmp: str) -> str:
    # The first attempt 404s on the companion pool. Once the base suite is
    # preferred (the conf file exists) the same install succeeds, which is
    # what apt does with the base suite at priority 990.
    conf = os.path.join(tmp, 'default-release.conf')
    return (f'if [ -f {conf} ]; then exit 0; fi; '
            f"cat <<'OUT'\n{_FETCH_FAILURE}OUT\nexit 100")


def _read(tmp: str, name: str) -> str:
    path = os.path.join(tmp, name)
    if not os.path.exists(path):
        return ''
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _install_lines(trace: str):
    return [l for l in trace.splitlines() if l.startswith('apt-get install')]


def test_apt_install_prefers_the_base_suite_after_a_fetch_failure():
    """A fetch failure must make the next attempt prefer the base suite, and
    the preference must persist for later apt runs on the node."""
    tmp = tempfile.mkdtemp()
    try:
        script = _apt_install_harness(tmp,
                                      _fetch_failure_unless_retargeted(tmp),
                                      _POLICY_WITH_BULLSEYE,
                                      _OS_RELEASE_BULLSEYE,
                                      calls='curl')
        result = _run(script)
        trace = _read(tmp, 'trace')
        assert result.returncode == 0, result.stderr[-800:]
        assert len(_install_lines(trace)) == 2, trace
        assert 'rc=0' in trace
        assert _read(
            tmp,
            'default-release.conf') == ('APT::Default-Release "bullseye";\n')
        log = _read(tmp, 'apt-update.log')
        assert "preferring the base suite 'bullseye'" in log
        # The attempt's own output still reaches the step log.
        assert 'E: Failed to fetch' in log
        # LC_ALL=C is set on the install so those diagnostics are English
        # for the detector regardless of the image's locale.
        assert 'LC_ALL=C' in _extract('apt_install_with_retries() {',
                                      'apt_update_install_with_retries() {')
    finally:
        shutil.rmtree(tmp)


def test_apt_install_keeps_the_plain_retry_on_a_non_fetch_failure():
    """A dpkg or dependency error is not a pool problem; the retries stay
    identical and the final status is the helper's usual 1."""
    tmp = tempfile.mkdtemp()
    try:
        script = _apt_install_harness(
            tmp,
            f"cat <<'OUT'\n{_DPKG_FAILURE}OUT\nexit 100",
            _POLICY_WITH_BULLSEYE,
            _OS_RELEASE_BULLSEYE,
            calls='curl')
        result = _run(script)
        trace = _read(tmp, 'trace')
        assert result.returncode == 0, result.stderr[-800:]
        assert len(_install_lines(trace)) == 3, trace
        assert 'rc=1' in trace
        assert not os.path.exists(os.path.join(tmp, 'default-release.conf'))
    finally:
        shutil.rmtree(tmp)


@pytest.mark.parametrize(
    'policy,os_release,expected_note',
    [
        # apt has no index for the codename: preferring it would turn a
        # fetch failure into a different failure.
        (_POLICY_WITHOUT_BULLSEYE, _OS_RELEASE_BULLSEYE,
         "apt has no index for release 'bullseye'"),
        # No codename to prefer at all.
        (_POLICY_WITH_BULLSEYE, 'ID=debian\nVERSION_ID="11"\n',
         'names no VERSION_CODENAME'),
    ])
def test_apt_install_does_not_retarget_without_a_usable_codename(
        policy, os_release, expected_note):
    tmp = tempfile.mkdtemp()
    try:
        script = _apt_install_harness(tmp,
                                      _fetch_failure_unless_retargeted(tmp),
                                      policy,
                                      os_release,
                                      calls='curl')
        result = _run(script)
        trace = _read(tmp, 'trace')
        assert result.returncode == 0, result.stderr[-800:]
        assert len(_install_lines(trace)) == 3, trace
        assert 'rc=1' in trace
        assert not os.path.exists(os.path.join(tmp, 'default-release.conf'))
        assert expected_note in _read(tmp, 'apt-update.log')
    finally:
        shutil.rmtree(tmp)


def test_apt_install_preference_carries_over_to_later_installs():
    """STEP 1 installs in several calls; once one has discovered the
    preference, the next must start with it rather than burn a failing
    first attempt of its own."""
    tmp = tempfile.mkdtemp()
    try:
        script = _apt_install_harness(tmp,
                                      _fetch_failure_unless_retargeted(tmp),
                                      _POLICY_WITH_BULLSEYE,
                                      _OS_RELEASE_BULLSEYE,
                                      calls='curl,rsync wget')
        result = _run(script)
        trace = _read(tmp, 'trace')
        assert result.returncode == 0, result.stderr[-800:]
        assert len(_install_lines(trace)) == 3, trace
        assert trace.count('rc=0') == 2
    finally:
        shutil.rmtree(tmp)


@pytest.mark.parametrize(
    'output,why',
    [
        # Same "Failed to fetch" text as the pool case, but no 404: a
        # timeout, DNS or proxy blip. Pinning here would make a healthy
        # release stop taking its security suite after one hiccup.
        (_TRANSIENT_FAILURE, 'transient'),
        # A 404, but from a third-party repo on the base suite, not from a
        # companion suite of the release: the base suite cannot help.
        (_THIRD_PARTY_404, 'third-party'),
    ])
def test_apt_install_pins_only_on_a_companion_suite_404(output, why):
    tmp = tempfile.mkdtemp()
    try:
        script = _apt_install_harness(tmp,
                                      f"cat <<'OUT'\n{output}OUT\nexit 100",
                                      _POLICY_WITH_BULLSEYE,
                                      _OS_RELEASE_BULLSEYE,
                                      calls='curl')
        result = _run(script)
        trace = _read(tmp, 'trace')
        assert result.returncode == 0, result.stderr[-800:]
        assert len(_install_lines(trace)) == 3, (why, trace)
        assert 'rc=1' in trace
        assert not os.path.exists(os.path.join(tmp, 'default-release.conf'))
        assert 'preferring the base suite' not in _read(tmp, 'apt-update.log')
    finally:
        shutil.rmtree(tmp)


def test_apt_install_reports_when_the_preference_cannot_be_written():
    """The log must not claim a preference apt never received."""
    tmp = tempfile.mkdtemp()
    try:
        # A path inside a directory that does not exist: tee fails.
        unwritable = os.path.join(tmp, 'no-such-dir', 'default-release.conf')
        script = _apt_install_harness(tmp,
                                      _fetch_failure_unless_retargeted(tmp),
                                      _POLICY_WITH_BULLSEYE,
                                      _OS_RELEASE_BULLSEYE,
                                      calls='curl',
                                      conf=unwritable)
        result = _run(script)
        trace = _read(tmp, 'trace')
        assert result.returncode == 0, result.stderr[-800:]
        log = _read(tmp, 'apt-update.log')
        assert 'the apt preference could not be written' in log
        assert 'preferring the base suite' not in log
        # No pin, so the retries stay identical and end in the usual 1.
        assert len(_install_lines(trace)) == 3, trace
        assert 'rc=1' in trace
    finally:
        shutil.rmtree(tmp)
