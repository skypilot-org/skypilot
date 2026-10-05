"""Latitude.sh provisioner glue: credentials + re-exports for the instance module.

The HTTP client itself lives in ``sky/adaptors/latitude.py`` and every call
it makes carries an explicit timeout. This module owns only the
provisioner-facing conventions:

* the key is read from ``LATITUDESH_API_KEY`` (the task secret channel's
  spelling) with ``~/.latitude/credentials`` (the skill manual's store,
  mode 600) as the file fallback — mirrors Spheron/Vast;
* the lane's fixed deploy knobs (project slug, ssh key name, default OS)
  are named once, here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from sky.adaptors import latitude as latitude_api

# The project the lane deploys into (find-or-create by slug; On-demand, so
# hourly servers are admissible — a Reserved project answers `yearly` only
# and would fail the deploy loudly at POST /servers).
PROJECT_SLUG = "skypilot"

# The name the lane's SSH key is registered under. ensure_ssh_key matches on
# key MATERIAL, not name — a rename never mints a duplicate and a re-created
# keypair with the same name never selects a stale key.
SSH_KEY_NAME = "skypilot"

# Plain Ubuntu 24.04 LTS (instant-deployment class): the bootstrap ladder
# installs the pinned CUDA runtime itself; the ML-in-a-box images ship
# their own toolchain, which we do not want fighting ours.
DEFAULT_OS = "ubuntu_24_04_x64_lts"

# Steady-state re-exports: the instance module's adoption guard refuses
# boxes in these states through `utils.STATUS_OFF` / `utils.STATUS_RESCUE_MODE`.
STATUS_OFF = latitude_api.STATUS_OFF
STATUS_RESCUE_MODE = latitude_api.STATUS_RESCUE_MODE
# Re-exports: the instance module speaks these through `utils.` so the
# adapter boundary stays one line.
LatitudeClient = latitude_api.LatitudeClient
LatitudeError = latitude_api.LatitudeError
LatitudeNotFoundError = latitude_api.LatitudeNotFoundError
STATUS_FAILED_DEPLOYMENT = latitude_api.STATUS_FAILED_DEPLOYMENT
server_status = latitude_api.LatitudeClient.server_status
primary_ipv4 = latitude_api.LatitudeClient.primary_ipv4


def client_from_env() -> LatitudeClient:
    """A client with the key from the env or the credential file.

    Reads the env FIRST (the controller's task secret channel), then
    ``~/.latitude/credentials`` (the manual's local store). An empty key
    raises LatitudeAuthError — never a client that would answer every call
    with 401 and read as "no capacity".
    """
    key = os.environ.get(latitude_api.API_KEY_ENV, "").strip()
    if not key:
        path = Path(latitude_api.API_KEY_FILE).expanduser()
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
    return LatitudeClient(key)


def resolve_api_key() -> Optional[str]:
    """The key from env or credential file, or None (for credential bridges)."""
    key = os.environ.get(latitude_api.API_KEY_ENV, "").strip()
    if key:
        return key
    path = Path(latitude_api.API_KEY_FILE).expanduser()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return None
