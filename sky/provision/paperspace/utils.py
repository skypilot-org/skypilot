"""Paperspace API client wrapper for SkyPilot."""

import json
import requests
import os
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Union

from sky import sky_logging
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

CREDENTIALS_PATH = "~/.paperspace/config.json"
API_ENDPOINT = "https://api.paperspace.com/v1"
INITIAL_BACKOFF_SECONDS = 10
MAX_BACKOFF_FACTOR = 10
MAX_ATTEMPTS = 6

REGIONS = [
    "East Coast (NY2)",
    "West Coast (CA1)",
    "Europe (AMS1)"
]

class PaperspaceCloudError(Exception):
    pass


def raise_paperspace_api_error(response: requests.Response) -> None:
    """Raise LambdaCloudError if appropriate."""
    status_code = response.status_code
    if status_code == 200:
        return
    if status_code == 429:
        # https://docs.lambdalabs.com/cloud/rate-limiting/
        raise PaperspaceCloudError("Your API requests are being rate limited.")
    try:
        resp_json = response.json()
        code = resp_json.get("error", {}).get("code")
        message = resp_json.get("error", {}).get("message")
    except json.decoder.JSONDecodeError as e:
        raise PaperspaceCloudError(
            "Response cannot be parsed into JSON. Status "
            f"code: {status_code}; reason: {response.reason}; "
            f"content: {response.text}"
        ) from e
    raise PaperspaceCloudError(f"{code}: {message}")


def _try_request_with_backoff(
    method: str, url: str, headers: Dict[str, str], data: Optional[str] = None
):
    backoff = common_utils.Backoff(
        initial_backoff=INITIAL_BACKOFF_SECONDS, max_backoff_factor=MAX_BACKOFF_FACTOR
    )
    for i in range(MAX_ATTEMPTS):
        if method == "get":
            response = requests.get(url, headers=headers)
        elif method == "post":
            response = requests.post(url, headers=headers, data=data)
        else:
            raise ValueError(f"Unsupported requests method: {method}")
        # If rate limited, wait and try again
        if response.status_code == 429 and i != MAX_ATTEMPTS - 1:
            time.sleep(backoff.current_backoff())
            continue
        if response.status_code == 200:
            return response
        raise_paperspace_api_error(response)


class PaperspaceCloudClient:
    """Wrapper functions for Paperspace and Machine Core API."""

    def __init__(self) -> None:
        self.credentials = os.path.expanduser(CREDENTIALS_PATH)
        assert os.path.exists(self.credentials), "Credentials not found"
        with open(self.credentials, "r") as f:
            self._credentials = json.load(f)
        self.api_key = self._credentials["apiKey"]
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    def list_instances(self) -> List[Dict[str, Optional[Union[str, Any]]]]:
        """
        Returns a list of all the instances with accompanying metadata
        """
        instances = []
        response = _try_request_with_backoff(
            "get",
            f"{API_ENDPOINT}/machines",
            headers=self.headers,
        ).json()
        instances.extend(response["items"])
        while response["hasMore"]:
            reponse = _try_request_with_backoff(
                "get",
                f"{API_ENDPOINT}/machines?after={response['nextPage']}",
                headers=self.headers,
            ).json()
            instances.extend(response["items"])
        return instances


        



