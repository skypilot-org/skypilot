"""Guard: client-advertised API version stays in lockstep with the server.

The dashboard hardcodes the API version it sends in the
``X-SkyPilot-API-Version`` header (sky/dashboard/.../constants.jsx). It ships
with the server, so it must equal sky/server/constants.py:API_VERSION. This
test fails if the two drift -- e.g. someone bumps ``API_VERSION`` for a new
API change without bumping the dashboard in the same change.

Both values are read by parsing the source files (no ``import sky``) so the
check stays hermetic and free of import side effects.
"""
import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SERVER_CONSTANTS = _REPO_ROOT / 'sky' / 'server' / 'constants.py'
_DASHBOARD_CONSTANTS = (_REPO_ROOT / 'sky' / 'dashboard' / 'src' / 'data' /
                        'connectors' / 'constants.jsx')


def _extract_int(path: pathlib.Path, pattern: str) -> int:
    match = re.search(pattern, path.read_text(encoding='utf-8'), re.MULTILINE)
    assert match is not None, f'{pattern!r} not found in {path}'
    return int(match.group(1))


def test_dashboard_client_api_version_matches_server():
    server_version = _extract_int(_SERVER_CONSTANTS,
                                  r'^API_VERSION\s*=\s*(\d+)')
    dashboard_version = _extract_int(_DASHBOARD_CONSTANTS,
                                     r"CLIENT_API_VERSION\s*=\s*'(\d+)'")
    assert dashboard_version == server_version, (
        f'Dashboard CLIENT_API_VERSION ({dashboard_version}) != server '
        f'API_VERSION ({server_version}). Bump {_DASHBOARD_CONSTANTS} in '
        f'lockstep (see the backward-compatibility guideline).')
