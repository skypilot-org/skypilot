import requests
from typing import Any, Dict, List
import json
import os
from functools import lru_cache
import uuid




def get_key_suffix():
    return str(uuid.uuid4()).replace("-", "")[:8]


ENDPOINT = "http//:localhost:5001/" # TODO(mjibril) change to live endpoint when ready
FLUIDSTACK_API_KEY_PATH = "~/.fluidstack/api_key"
FLUIDSTACK_API_TOKEN_PATH = "~/.fluidstack/api_token"


def read_contents(path: str) -> str:
    try:
        with open(path, mode="r") as f:
            return f.read().strip()
    except FileNotFoundError:
        raise


class FluidstackAPIError(Exception):
    pass


def raise_fluidstack_error(response: requests.Response) -> None:
    """Raise FluidstackError if appropriate."""
    status_code = response.status_code
    if response.ok:
        return
    try:
        resp_json = response.json()
        message = resp_json.get("error", response.text)
    except (KeyError, json.decoder.JSONDecodeError):
        raise FluidstackAPIError(f"Unexpected error. Status code: {status_code}")
    raise FluidstackAPIError(f"{message}")


class FluidstackClient:
    def __init__(self):
        self.api_key = read_contents(os.path.expanduser(FLUIDSTACK_API_KEY_PATH))
        self.api_token = read_contents(os.path.expanduser(FLUIDSTACK_API_TOKEN_PATH))

    def list_instances(self) -> List[Dict[str, Any]]:
        response = requests.get(
            ENDPOINT + "api2/list",
            auth=(self.api_key, self.api_token),
        )
        raise_fluidstack_error(response)
        return response.json()

    def create_instance(
        self,
        instance_type: str = "94d76997-44ec-4f1e-8291-231de42b6030",
        region: str = "Norway 2, EU",
        ssh_pub_key: str = "",
    ) -> List[str]:
        """Launch new instances."""
        regions = self.list_regions()

        ssh_key = self.get_or_add_ssh_key(ssh_pub_key)
        body = dict(
            plan=instance_type,
            region=regions[region],
            os="Ubuntu 20.04 LTS",
            ssh_keys=[ssh_key["id"]],
        )

        response = requests.post(
            ENDPOINT + "api2/deploy", auth=(self.api_key, self.api_token), json=body
        )
        raise_fluidstack_error(response)
        return response.json().get("server", {}).get("id")

    @lru_cache
    def list_ssh_keys(self):
        response = requests.get(
            ENDPOINT + "api/ssh_key", auth=(self.api_key, self.api_token)
        )
        raise_fluidstack_error(response)
        return response.json()["ssh_keys"]

    def get_or_add_ssh_key(self, ssh_pub_key: str = None) -> None:
        """Add ssh key if not already added."""
        self.list_ssh_keys.clear_cache()
        ssh_keys = self.list_ssh_keys()
        for key in ssh_keys:
            if key["Public_Key"].strip() == ssh_pub_key.strip():
                return {"id": key["id"], "name": key["Name"], "ssh_key": ssh_pub_key}
        ssh_key_name = "skypilot-" + get_key_suffix()
        response = requests.post(
            ENDPOINT + "api/ssh_key",
            auth=(self.api_key, self.api_token),
            json=dict(Name=ssh_key_name, Public_Key=ssh_pub_key),
        )
        raise_fluidstack_error(response)
        key_id = response.json()["key_id"]
        return {"id": key_id, "name": ssh_key_name, "ssh_key": ssh_pub_key}

    @lru_cache()
    def list_regions(self):
        response = requests.get(ENDPOINT + "api/plans")
        raise_fluidstack_error(response)
        plans = response.json()
        plans = [
            plan
            for plan in plans
            if plan["minimum_commitment"] == "hourly"
            and plan["type"] in ["preconfigured"]
            and plan["gpu_type"] != "NO GPU"
        ]

        def get_regions(plans: List) -> dict:
            """Return a list of regions where the plan is available."""
            regions = {}
            for plan in plans:
                for region in plan.get("regions", []):
                    regions[region["slug"]] = region["id"]
            return regions

        regions = get_regions(plans)
        return regions

    def delete(self, instance_id: str):
        response = requests.delete(
            ENDPOINT + "api2/delete",
            auth=(self.api_key, self.api_token),
            json=dict(server=instance_id),
        )
        raise_fluidstack_error(response)
        return response.json()

    def stop(self, instance_id: str):
        response = requests.post(
            ENDPOINT + "api2/stop",
            auth=(self.api_key, self.api_token),
            json=dict(server=instance_id),
        )
        raise_fluidstack_error(response)
        return response.json()

    def restart(self, instance_id: str):
        response = requests.post(
            ENDPOINT + "api2/restart",
            auth=(self.api_key, self.api_token),
            json=dict(server=instance_id),
        )
        raise_fluidstack_error(response)
        return response.json()

    def info(self, instance_id: str):
        response = requests.get(
            ENDPOINT + f"api2/list/{instance_id}", auth=(self.api_key, self.api_token)
        )
        raise_fluidstack_error(response)
        return response.json()

    def status(self, instance_id: str):
        response = requests.get(
            ENDPOINT + f"api2/status/{instance_id}",
            auth=(self.api_key, self.api_token),
            json=dict(server=instance_id),
        )
        raise_fluidstack_error(response)
        return response.json()["status"]

    def add_tags(self, instance_id: str, tags: Dict[str, str]):
        response = requests.post(
            ENDPOINT + f"api2/tag",
            auth=(self.api_key, self.api_token),
            json=dict(instance_id=instance_id, tags=json.dumps(tags)),
        )
        raise_fluidstack_error(response)
        return response.json()
