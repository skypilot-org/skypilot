"""Prime Intellect provisioner glue: credentials + re-exports for the
instance module.

The HTTP client itself lives in ``sky/adaptors/primeintellect.py`` and
every call it makes carries an explicit timeout. This module owns only the
provisioner-facing conventions:

* the key is read from ``PRIME_INTELLECT_API_KEY`` (the task secret
  channel's spelling — the ONLY spelling; the vendor publishes no env
  convention and there is no alias to normalize) with
  ``~/.prime-intellect/credentials`` (mode 600) as the file fallback —
  mirrors Spheron/Vast/Latitude/QuantaCloud;
* the lane's fixed deploy knobs (ssh key name) are named once, here.
"""

import os
from pathlib import Path
from typing import Optional

from sky.adaptors import primeintellect as primeintellect_api

# The name the lane's SSH key is registered under. ensure_ssh_key matches
# on key MATERIAL (this API's key list returns the public key verbatim),
# so a rename never mints a duplicate and a re-created keypair with the
# same name never selects a stale key.
SSH_KEY_NAME = 'skypilot'

# Re-exports: the instance module speaks these through ``utils.`` so the
# adapter boundary stays one line.
PrimeIntellectClient = primeintellect_api.PrimeIntellectClient
PrimeintellectError = primeintellect_api.PrimeintellectError
PrimeintellectAuthError = primeintellect_api.PrimeintellectAuthError
PrimeintellectNotFoundError = primeintellect_api.PrimeintellectNotFoundError
PrimeintellectResourcesUnavailableError = (
    primeintellect_api.PrimeintellectResourcesUnavailableError)
STATUS_ACTIVE = primeintellect_api.STATUS_ACTIVE
STATUS_ERROR = primeintellect_api.STATUS_ERROR
STATUS_STOPPED = primeintellect_api.STATUS_STOPPED
STATUS_DELETING = primeintellect_api.STATUS_DELETING
STATUS_TERMINATED = primeintellect_api.STATUS_TERMINATED
STATUS_UNKNOWN = primeintellect_api.STATUS_UNKNOWN
pod_status = primeintellect_api.PrimeIntellectClient.pod_status
ssh_user = primeintellect_api.PrimeIntellectClient.ssh_user
ssh_port = primeintellect_api.PrimeIntellectClient.ssh_port
pod_ip = primeintellect_api.PrimeIntellectClient.pod_ip
parse_ssh_connection = primeintellect_api.parse_ssh_connection


def client_from_env() -> PrimeIntellectClient:
    """A client with the key from the env or the credential file.

    Reads the env FIRST (the controller's task secret channel), then
    ``~/.prime-intellect/credentials`` (the lane's local store). An empty
    key raises PrimeintellectAuthError — never a client that would answer
    every authenticated call with 401/403 and read as "no capacity".
    """
    key = os.environ.get(primeintellect_api.API_KEY_ENV, '').strip()
    if not key:
        path = Path(primeintellect_api.API_KEY_FILE).expanduser()
        if path.is_file():
            key = path.read_text(encoding='utf-8').strip()
    return PrimeIntellectClient(key)


def resolve_api_key() -> Optional[str]:
    """The key from env or credential file, or None (for credential bridges)."""
    key = os.environ.get(primeintellect_api.API_KEY_ENV, '').strip()
    if key:
        return key
    path = Path(primeintellect_api.API_KEY_FILE).expanduser()
    if path.is_file():
        return path.read_text(encoding='utf-8').strip()
    return None
