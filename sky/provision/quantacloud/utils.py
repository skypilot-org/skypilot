"""QuantaCloud provisioner glue: credentials + re-exports for the
instance module.

The HTTP client itself lives in ``sky/adaptors/quantacloud.py`` and every
call it makes carries an explicit timeout. This module owns only the
provisioner-facing conventions:

* the key is read from ``QUANTACLOUD_API_KEY`` (the task secret channel's
  spelling — the ONLY spelling; the vendor has no alias convention) with
  ``~/.quanta/credentials`` (the skill manual's store, mode 600) as the
  file fallback — mirrors Spheron/Vast/Latitude;
* the lane's fixed deploy knobs (ssh key name) are named once, here.
"""

import os
from pathlib import Path
from typing import Optional

from sky.adaptors import quantacloud as quantacloud_api

# The name the lane's SSH key is registered under. ensure_ssh_key matches
# on key MATERIAL (via the SHA256 fingerprint derived from the public key
# blob — the key list returns fingerprints, not material), so a rename
# never mints a duplicate and a re-created keypair with the same name
# never selects a stale key.
SSH_KEY_NAME = 'skypilot'

# Re-exports: the instance module speaks these through ``utils.`` so the
# adapter boundary stays one line.
QuantacloudClient = quantacloud_api.QuantacloudClient
QuantacloudError = quantacloud_api.QuantacloudError
QuantacloudNotFoundError = quantacloud_api.QuantacloudNotFoundError
STATUS_ACTIVE = quantacloud_api.STATUS_ACTIVE
STATUS_FAILED = quantacloud_api.STATUS_FAILED
STATUS_INTERRUPTED = quantacloud_api.STATUS_INTERRUPTED
STATUS_TERMINATED = quantacloud_api.STATUS_TERMINATED
deployment_status = quantacloud_api.QuantacloudClient.deployment_status
ssh_user = quantacloud_api.QuantacloudClient.ssh_user


def client_from_env() -> QuantacloudClient:
    """A client with the key from the env or the credential file.

    Reads the env FIRST (the controller's task secret channel), then
    ``~/.quanta/credentials`` (the manual's local store). An empty key
    raises QuantacloudAuthError — never a client that would answer every
    authenticated call with 401 and read as "no capacity".
    """
    key = os.environ.get(quantacloud_api.API_KEY_ENV, '').strip()
    if not key:
        path = Path(quantacloud_api.API_KEY_FILE).expanduser()
        if path.is_file():
            key = path.read_text(encoding='utf-8').strip()
    return QuantacloudClient(key)


def resolve_api_key() -> Optional[str]:
    """The key from env or credential file, or None (for credential bridges)."""
    key = os.environ.get(quantacloud_api.API_KEY_ENV, '').strip()
    if key:
        return key
    path = Path(quantacloud_api.API_KEY_FILE).expanduser()
    if path.is_file():
        return path.read_text(encoding='utf-8').strip()
    return None
