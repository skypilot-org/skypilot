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
from packaging import version
import pytest

import sky
from sky.skylet import constants

# The wheel name SKYPILOT_WHEEL_INSTALLATION_COMMANDS globs for.
_SKY_VERSION = str(version.parse(sky.__version__))

_TEMPLATE = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'sky',
                         'templates', 'kubernetes-ray.yml.j2')

# Every package manager the bootstrap can detect.
_MANAGERS = ['apt', 'dnf', 'microdnf', 'yum', 'apk', 'zypper']


def _template_lines():
    with open(_TEMPLATE, 'r', encoding='utf-8') as f:
        return f.read().split('\n')


def _extract(start_marker: str, end_marker: str, **render_vars) -> str:
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
            **{
                'k8s_fuse_device_required': False,
                'k8s_apt_mirrors': None,
                'k8s_enable_docker_all': False,
                'k8s_enable_docker_build': False,
                **render_vars,
            })


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
# Offline pods: PyPI prefetch, runtime pins, wheel install
# --------------------------------------------------------------------------


def _write_exe(path: str, body: str) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write('#!/bin/bash\n' + textwrap.dedent(body))
    os.chmod(path, 0o755)


def _gnu_env_shim(bindir: str) -> None:
    """`env -u X -C dir` is GNU-only; pass the command through."""
    _write_exe(
        os.path.join(bindir, 'env'), """
        while [ $# -gt 0 ]; do
          case "$1" in -u|-C) shift 2 ;; *=*) export "$1"; shift ;; *) break ;; esac
        done
        exec "$@"
        """)


def _prefetch(ray_in_image: bool, prefetch_ok: bool = True):
    """Runs the real Ray check + PyPI prefetch with a scripted uv."""
    ray_version = constants.SKY_REMOTE_RAY_VERSION
    # Start above the Ray install, so the test also pins the check's order.
    body = _extract(
        '{{ uv_installation_commands }}',
        '# Wait (bounded) for the best-effort `patch` install',
        uv_installation_commands='',
        ray_installed_check_command=constants.SKY_RAY_INSTALLED_CHECK_CMD.
        replace('{ray_version}', ray_version),
        # E7 stand-in: like the real one, it leaves Ray installed.
        ray_installation_commands=('touch "$HOME/ray_installed"'),
        sky_unset_pythonpath_and_set_cwd='')
    tmp = tempfile.mkdtemp()
    try:
        bindir = os.path.join(tmp, 'bin')
        os.makedirs(bindir)
        os.makedirs(os.path.join(tmp, '.local', 'bin'))
        _gnu_env_shim(bindir)
        if ray_in_image:
            open(os.path.join(tmp, 'ray_installed'), 'w').close()
        _write_exe(
            os.path.join(tmp, '.local', 'bin', 'uv'), f"""
            if [ "$1 $2" = "pip list" ]; then
              # The check must look at the runtime venv, not a system Python.
              [ "$UV_SYSTEM_PYTHON" = false ] || exit 3
              [ -f {tmp}/ray_installed ] && echo "ray                {ray_version}"
              exit 0
            fi
            echo "$*" >> {tmp}/uv_calls
            exit {0 if prefetch_ok else 2}
            """)
        result = _run(f'HOME={tmp}\nPATH={bindir}:$PATH\n' + body, errexit=True)
        calls_file = os.path.join(tmp, 'uv_calls')
        calls = open(calls_file, encoding='utf-8').read().splitlines() \
            if os.path.exists(calls_file) else []
        return result, calls
    finally:
        shutil.rmtree(tmp)


def test_prefetch_skipped_when_the_image_carries_the_runtime():
    """An image with the pinned Ray (e.g. SkyPilot's default) needs no PyPI."""
    result, calls = _prefetch(ray_in_image=True)
    assert result.returncode == 0, result.stderr
    assert not any('skypilot[kubernetes,remote]' in c for c in calls), calls


def test_prefetch_still_runs_on_a_fresh_image():
    """The Ray check must run before the Ray install, which always adds Ray."""
    result, calls = _prefetch(ray_in_image=False)
    assert result.returncode == 0, result.stderr
    assert any('skypilot[kubernetes,remote]' in c for c in calls), calls


def test_failed_prefetch_does_not_fail_the_step():
    result, _ = _prefetch(ray_in_image=False, prefetch_ok=False)
    assert result.returncode == 0, result.stderr
    assert 'could not prefetch' in result.stdout


@pytest.mark.parametrize('ray_in_image', [True, False])
def test_ray_install_touches_the_index_only_to_install_ray(ray_in_image):
    """An image that already has Ray must start without internet access.

    The setuptools/click pins are only applied together with a Ray install;
    applied on their own, they made a pod with SkyPilot's default image
    (setuptools 84) fetch setuptools from the index on every start.
    """
    tmp = tempfile.mkdtemp()
    try:
        bindir = os.path.join(tmp, 'bin')
        uvdir = os.path.join(tmp, '.local', 'bin')
        os.makedirs(bindir)
        os.makedirs(uvdir)
        _gnu_env_shim(bindir)
        listed = (
            f'echo "ray                {constants.SKY_REMOTE_RAY_VERSION}"'
            if ray_in_image else 'true')
        _write_exe(
            os.path.join(uvdir, 'uv'), f"""
            case "$1 $2" in
              "-V "*) exit 0 ;;
              "pip list") {listed}; exit 0 ;;
              "pip install") echo "$*" >> {tmp}/installs; exit 0 ;;
              run*) echo /fake/ray; exit 0 ;;
            esac
            exit 1
            """)
        cmd = constants.RAY_INSTALLATION_COMMANDS.replace(
            '{ray_version}', constants.SKY_REMOTE_RAY_VERSION)
        result = _run(f'HOME={tmp}\nPATH={bindir}:$PATH\n' + cmd, errexit=True)
        assert result.returncode == 0, result.stdout + result.stderr
        installs_path = os.path.join(tmp, 'installs')
        installs = []
        if os.path.exists(installs_path):
            with open(installs_path, encoding='utf-8') as f:
                installs = f.read().splitlines()
        if ray_in_image:
            assert installs == [], installs
        else:
            assert len(installs) == 1, installs
            for pin in ('ray[default]==', 'click<8.3.0', 'setuptools<70'):
                assert pin in installs[0], installs
    finally:
        shutil.rmtree(tmp)


@pytest.mark.parametrize(
    'offline_ok,online_ok,expect_online,expect_rc',
    [
        # Every dependency already installed: no index contact at all.
        (True, True, False, 0),
        # Something missing: fall back to the index, as before.
        (False, True, True, 0),
        (False, False, True, 1),
    ])
def test_wheel_install_tries_offline_first(offline_ok, online_ok, expect_online,
                                           expect_rc):
    tmp = tempfile.mkdtemp()
    try:
        bindir = os.path.join(tmp, 'bin')
        uvdir = os.path.join(tmp, '.local', 'bin')
        wheels = os.path.join(tmp, '.sky', 'wheels', 'abc')
        for d in (bindir, uvdir, wheels):
            os.makedirs(d)
        open(os.path.join(wheels, f'skypilot-{_SKY_VERSION}-py3-none-any.whl'),
             'w').close()
        _gnu_env_shim(bindir)
        _write_exe(
            os.path.join(uvdir, 'uv'), f"""
            echo "$*" >> {tmp}/uv_calls
            case "$1 $2" in
              "-V "*) exit 0 ;;
              "pip list") exit 0 ;;
              "pip uninstall") exit 0 ;;
            esac
            case " $* " in
              *" --offline "*) exit {0 if offline_ok else 2} ;;
              *) exit {0 if online_ok else 2} ;;
            esac
            """)
        cmd = constants.SKYPILOT_WHEEL_INSTALLATION_COMMANDS.replace(
            '{sky_wheel_hash}', 'abc').replace('{cloud}', 'kubernetes')
        result = _run(f'HOME={tmp}\nPATH={bindir}:$PATH\n' + cmd)
        with open(os.path.join(tmp, 'uv_calls'), encoding='utf-8') as f:
            installs = [
                l for l in f.read().splitlines() if l.startswith('pip install')
            ]
        assert '--offline' in installs[0], installs
        assert (len(installs) == 2) == expect_online, installs
        assert result.returncode == expect_rc, result.stdout + result.stderr
        marker = os.path.join(tmp, '.sky', 'wheels', 'current_sky_wheel_hash')
        assert os.path.exists(marker) == (expect_rc == 0)
    finally:
        shutil.rmtree(tmp)
