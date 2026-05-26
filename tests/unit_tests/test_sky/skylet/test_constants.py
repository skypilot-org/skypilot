"""Unit tests for sky/skylet/constants.py runtime command templates."""
import re
import subprocess

from sky.server import common as server_common
from sky.skylet import constants


def _render_wheel_install_cmd() -> str:
    return constants.SKYPILOT_WHEEL_INSTALLATION_COMMANDS.replace(
        '{sky_wheel_hash}', 'abc123').replace('{cloud}', 'aws')


def test_wheel_install_command_is_valid_bash():
    """The rendered wheel-install snippet must parse as bash."""
    rendered = _render_wheel_install_cmd()
    result = subprocess.run(['bash', '-n', '-c', rendered],
                            capture_output=True,
                            text=True,
                            check=False)
    assert result.returncode == 0, (
        f'bash -n rejected the rendered wheel-install command: '
        f'{result.stderr}\n---\n{rendered}')


def test_wheel_install_kills_stale_api_server():
    """After reinstalling skypilot, the script must kill any local API server.

    Long-lived local API servers on jobs/serve controllers cache
    sky.__version__ at import time. If the wheel is reinstalled underneath
    them, wheel_utils._build_sky_wheel will refuse later launches with
    'The installed SkyPilot version is different from the running code',
    surfacing on SkyServe as replica FAILED_PROVISION after 3 retries.
    """
    rendered = _render_wheel_install_cmd()
    # The kill must live inside the reinstall branch (after the
    # uv pip install ... && echo ... > current_sky_wheel_hash || exit 1).
    install_marker = 'current_sky_wheel_hash'
    install_idx = rendered.rfind(install_marker)
    assert install_idx != -1, rendered
    tail = rendered[install_idx:]
    # The regex uses a [s] character class to avoid pkill matching the parent
    # shell when the whole setup script is run via `bash -c "..."`.
    assert re.search(r'pkill\s+-f\s+"\[s\]ky\.server\.server"', tail), (
        f'Expected pkill of the local API server after wheel reinstall, '
        f'got tail: {tail!r}')
    # The pkill must tolerate "no such process" (the common case on
    # ordinary user clusters / replica VMs) so it does not break setup.
    # In the debug-instrumented variant, pkill's exit code is captured into
    # $pkill_rc instead of swallowed with `|| true`; the surrounding shell
    # must NOT have an `|| exit 1` after pkill, or no-match would abort the
    # whole setup. Assert that by checking nothing aborts the script after
    # pkill returns non-zero.
    assert '|| true' in tail or 'pkill_rc=$?' in tail, (
        f'pkill must tolerate the no-match exit code, got tail: {tail!r}')


def test_wheel_install_pkill_pattern_matches_api_server_cmd():
    """The pkill pattern must actually match sky.server.common.API_SERVER_CMD.

    server_common.API_SERVER_CMD is the cmdline the local API server is
    spawned with and is the same string sdk._local_api_server_running()
    greps for. If the two ever drift, the reinstall path will leave a
    zombie API server behind even though pkill ran.
    """
    rendered = _render_wheel_install_cmd()
    match = re.search(r'pkill\s+-f\s+"([^"]+)"', rendered)
    assert match is not None, rendered
    pattern = match.group(1)
    assert re.search(pattern, server_common.API_SERVER_CMD), (
        f'pkill pattern {pattern!r} does not match '
        f'server_common.API_SERVER_CMD={server_common.API_SERVER_CMD!r}')
    # Defensive: the `[s]ky...` bracket-class trick must keep the pattern
    # itself out of the literal text. If someone simplified it back to
    # "sky.server.server" the parent shell of a `bash -c "..."` setup run
    # would self-kill.
    assert not re.search(pattern, pattern), (
        f'pkill pattern {pattern!r} matches its own literal text — it would '
        f'kill the parent shell when the setup script is run via bash -c.')
