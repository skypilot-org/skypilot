"""DigitalOcean API client wrapper for SkyPilot."""

import json
import os
import pydo
import requests
import time
import yaml
from typing import Any, Dict, List, Optional, Union

from azure.core.pipeline import policies

from sky import sky_logging
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

MAX_BACKOFF_FACTOR: int = 10
MAX_ATTEMPTS: int = 6

# locations set by doctl CLI
POSSIBLE_CREDENTIALS: List[str] = [
    '~/Library/Application Support/doctl/config.yaml', # Mac OS
    os.path.join(os.getenv('XDG_CONFIG_HOME', '~/.config'), 'doctl/config.yaml'), # Linux
    os.path.join(os.getenv('APPDATA', ''), r'\doctl\config.yaml'), # Windows
]
SSH_KEY_NAME = f'skypilot-ssh-{common_utils.get_user_hash()}'
client = None


class DigitalOceanCloudError(Exception):
    pass


def init_client() -> None:
    global client

    credentials_path = None
    for path in POSSIBLE_CREDENTIALS:
        path = os.path.expanduser(path)
        if os.path.exists(path):
            credentials_path = path
            break

    if credentials_path is None:
        raise ValueError(f'no valid DigitalOcean config found from {POSSIBLE_CREDENTIALS}')
    logger.debug(f"using DigitalOcean config located at {credentials_path}")
    with open(credentials_path, 'r', encoding='utf-8') as f:
        credentials: Dict[str, Any] = yaml.safe_load(f)
    api_token = credentials['auth-contexts']['skypilot']
    client = pydo.Client(
        token=api_token, 
        retry_policy=policies.RetryPolicy(
            retry_total=MAX_ATTEMPTS,
            retry_backoff_factor=MAX_BACKOFF_FACTOR
        ))
        

def get_or_create_ssh_keys(public_key: str) -> None:
    ssh_keys = client.ssh_keys.list()
    for key in ssh_keys['ssh_keys']:
        if key['name'] == SSH_KEY_NAME:
            return
    
    pydo.ssh_keys.create({
        'public_key' : public_key,
        'name' : SSH_KEY_NAME,
    })

init_client()
        