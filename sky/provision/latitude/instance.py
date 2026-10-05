"""Latitude.sh instance provisioning.

In-tree provider (the fork carries it; see skypilot-controller's
``pyproject.toml`` carry inventory). Exposes the exact surface
``sky.provision`` dispatches to, mirroring ``sky/provision/spheron/instance.py``.

Lifecycle:

* ``run_instances`` — create (or adopt) the cluster's single bare-metal
  server and wait until it is ``on``: ensure the On-demand project (slug
  ``skypilot``), ensure the SSH key by key MATERIAL, ``POST /servers``
  (hourly billing, Ubuntu 24.04 LTS, ssh_keys attached), then poll. The
  poll fails CLOSED on ``failed_deployment`` and on a provisioning slug
  that outlives the deadline — a stuck deploy must become a failed runner,
  not an endless wait (the failure mode that kept a Vast instance
  ``SkyPilotProvisioning`` for 30 minutes after the provider gave up).
* ``terminate_instances`` — ``DELETE /servers/{id}``; the ONLY way to stop
  hourly billing (``power_off`` keeps the meter running). 404 is success
  (idempotent teardown). A delete that raises leaves the server LIVE and
  billing, so the error propagates — never report success over a live box.
* ``query_instances`` — exact-hostname lookup, statuses mapped, unknown
  provisioning slugs logged verbatim (``status`` is an open set).

Every HTTP call carries an explicit timeout inside the client
(``sky/adaptors/latitude.py``); nothing here may call the API any other way.
"""

from __future__ import annotations

import os
import typing
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.latitude import utils
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    pass

logger = sky_logging.init_logger(__name__)

PROVIDER_NAME = "latitude"

# Bare metal with the stock Ubuntu image: root over SSH, port 22, no NAT
# port mapping (unlike Vast's proxy dance).
# MEASURED 2026-10-05 (ENG-507 CPU smoke, m4-metal-small @ DAL): the stock
# ubuntu_24_04_x64_lts image REFUSES root over SSH ("Permission denied
# (publickey)", 12 retries) and authenticates the SAME key as `ubuntu`
# with passwordless sudo — the massed-compute/Spheron convention, not the
# skill manual's `ssh root@<primary_ipv4>`. SkyPilot sudo's for its setup,
# so only the login name matters.
SSH_USER = "ubuntu"
SSH_PORT = 22

# The literal the ray template carries until `configure_ssh_info` substitutes
# the real key into node_config['PublicKey'] (see the check in run_instances
# for why the literal itself must be refused).
_SSH_PUBLIC_KEY_PLACEHOLDER = "skypilot:ssh_public_key_content"

# Latitude server status -> SkyPilot cluster status.
#
# Latitude's `status` is an OPEN set: while provisioning, the platform
# returns its provisioning slug verbatim (`queued`, `starting_deploy`,
# `commissioning`, ...) and the docs forbid treating the field as a closed
# enum. Only the steady states are mapped; anything else maps to None =
# transitional (SkyPilot keeps waiting), except `failed_deployment`, which
# is a terminal failure surfaced as TERMINATED so reconcile reaps it.
_STATUS_MAP: Dict[str, Optional[status_lib.ClusterStatus]] = {
    "on": status_lib.ClusterStatus.UP,
    "off": status_lib.ClusterStatus.STOPPED,
    "unknown": None,
    "deploying": None,
    "failed_deployment": status_lib.ClusterStatus.STOPPED,
    "disk_erasing": None,
    "rescue_mode": status_lib.ClusterStatus.STOPPED,
    "entering_rescue_mode": None,
    "exiting_rescue_mode": None,
}


def _client() -> "utils.LatitudeClient":
    return utils.client_from_env()


def _sky_status(server: Dict[str, Any]):
    """Map one server's status, refusing to guess at an unknown steady state.

    Unmapped slugs are provisioning states (open set) and map to None
    (still in flight). The KNOWN terminal failure `failed_deployment` maps
    to STOPPED — present (so it is never dropped from capacity reports) but
    not UP (so nothing schedules onto a dead box).
    """
    raw = utils.server_status(server)
    return _STATUS_MAP.get(raw)


def _is_live(server: Dict[str, Any]) -> bool:
    """A server that still exists (and bills) in any state.

    The only non-live state is deletion-complete, which the provider
    expresses by the server VANISHING from list endpoints, not by a status
    slug. So every server we can see is live; this predicate exists so the
    adoption path reads correctly and the reconciliation can't orphan a box.
    """
    return True


def run_instances(
    region: str,
    cluster_name: str,
    cluster_name_on_cloud: str,
    config: common.ProvisionConfig,
) -> common.ProvisionRecord:
    """Create (or adopt) the cluster's single server and wait for `on`."""
    del cluster_name  # cluster_name_on_cloud is the identity we use
    client = _client()

    servers = client.list_servers(hostname=cluster_name_on_cloud)
    live = [s for s in servers if _is_live(s)]
    if len(live) > 1:
        raise utils.LatitudeError(
            f"cluster {cluster_name_on_cloud!r} has {len(live)} live servers; "
            "refusing to guess which is the head"
        )

    created = []
    if live:
        server = live[0]
        status = utils.server_status(server)
        if status == utils.STATUS_FAILED_DEPLOYMENT:
            raise utils.LatitudeError(
                f"server {server.get('id')} for cluster "
                f"{cluster_name_on_cloud!r} is in failed_deployment; "
                "terminate it before relaunching (not auto-retried — "
                "report and decide)"
            )
        if status in (utils.STATUS_OFF, utils.STATUS_RESCUE_MODE):
            # Adopted boxes in these steady states can never reach `on` on
            # their own: `off` is a hard power-off and `rescue_mode` is a
            # rescue boot, and this integration never issues power_on /
            # exit-rescue. Waiting would block the full poll timeout
            # (25 min) before failing — the same dead wait the Spheron
            # lane's stopped-guard exists to prevent (CodeRabbit on the
            # PR). Transitional slugs (queued/starting_deploy/...) stay
            # unmapped and keep waiting.
            raise utils.LatitudeError(
                f"server {server.get('id')} for cluster "
                f"{cluster_name_on_cloud!r} is {status!r}; it cannot become "
                "`on` on its own and this integration does not power boxes "
                "back on — terminate it before relaunching"
            )
    else:
        node_config = config.node_config
        plan = node_config.get("InstanceType")
        if not plan:
            raise utils.LatitudeError(
                'node_config is missing InstanceType (the Latitude plan slug, '
                "e.g. g4-rtx6kpro-large)"
            )
        operating_system = (
            node_config.get("latitude_operating_system") or utils.DEFAULT_OS
        )

        # node_config['PublicKey'] is the ONE canonical source: backend_utils
        # puts Latitude on the generic `auth.configure_ssh_info` path, which
        # substitutes the real key into the template's
        # `skypilot:ssh_public_key_content` placeholder. The placeholder
        # check is the point: if substitution did NOT run, the literal is
        # still TRUTHY, a bare `if not public_key` waves garbage into
        # ensure_ssh_key, and the server fails far from the cause (the same
        # trap the Spheron carry documented).
        public_key = (config.node_config or {}).get("PublicKey")
        if isinstance(public_key, str):
            public_key = public_key.strip()
        if not public_key or public_key == _SSH_PUBLIC_KEY_PLACEHOLDER:
            raise utils.LatitudeError(
                "node_config['PublicKey'] is missing or was never "
                f"substituted (got {public_key!r}); auth.configure_ssh_info "
                "must run before provisioning — a Latitude server without "
                "a real public key is unreachable"
            )

        # The deploy API takes a project ID or slug; the lane's On-demand
        # project is found-or-created by slug so hourly deploys are
        # admissible (Reserved projects only accept `yearly`).
        project = client.ensure_project(utils.PROJECT_SLUG)
        ssh_key_id = client.ensure_ssh_key(utils.SSH_KEY_NAME, public_key)

        server = client.create_server(
            project=project,
            plan=str(plan),
            site=region,
            operating_system=str(operating_system),
            hostname=cluster_name_on_cloud,
            ssh_key_ids=[ssh_key_id],
            billing="hourly",
        )
        created = [str(server["id"])]

    server_id = str(server["id"])
    # Readiness is `status == on` (SSH answers on primary_ipv4 only then);
    # the poll fails closed on failure and on deadline.
    client.wait_until_on(server_id)

    return common.ProvisionRecord(
        provider_name=PROVIDER_NAME,
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=server_id,
        resumed_instance_ids=[],
        created_instance_ids=created,
    )


def wait_instances(
    region: str, cluster_name_on_cloud: str,
    state: Optional[status_lib.ClusterStatus]
) -> None:
    """No-op: run_instances already blocks until `on`."""
    del region, cluster_name_on_cloud, state

def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """STOP is refused, not silently destroy-terminated (CodeRabbit on the PR).

    The dispatch contract (sky/provision/__init__.py stop_instances wrapper
    after `provider_name` is stripped) is this signature — the
    `(region, cluster_name, ...)` shape copied from vast does not BIND and
    would TypeError at dispatch. Latitude declares STOP unsupported in
    Latitude._CLOUD_UNSUPPORTED_FEATURES (power_off does NOT stop hourly
    billing — only DELETE does), so SkyPilot never routes here; if anything
    ever does, refuse loudly rather than terminating the box as a "stop",
    which destroys the disk while the caller believed it was preserved.
    """
    del cluster_name_on_cloud, provider_config, worker_only
    raise NotImplementedError(
        "stop is unsupported on Latitude: power_off keeps billing hourly, "
        "and DELETE destroys the disk. Use terminate_instances for teardown."
    )


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Terminate the cluster's servers (see module docstring: DELETE or bill)."""
    del provider_config, worker_only  # single-node lane; the head is the cluster
    if not str(cluster_name_on_cloud or "").strip():
        # An empty identity filters NOTHING on Latitude: list_servers would
        # return EVERY server on the team and this loop would delete the
        # whole account's fleet (CodeRabbit on the PR). Refuse loudly.
        raise utils.LatitudeError(
            "refusing to terminate with an empty cluster_name_on_cloud: "
            "that filters nothing and would delete every server on the "
            "account"
        )
    client = _client()
    for server in client.list_servers(hostname=cluster_name_on_cloud):
        if not _is_live(server):
            continue
        server_id = str(server["id"])
        try:
            client.delete_server(server_id)
        except utils.LatitudeNotFoundError:
            # Already deleted server-side: teardown is idempotent.
            logger.info("server %s already gone on delete", server_id)


def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    del region, provider_config
    client = _client()
    servers = [
        s for s in client.list_servers(hostname=cluster_name_on_cloud)
        if _is_live(s)
    ]
    if not servers:
        return common.ClusterInfo(
            instances={}, head_instance_id=None, provider_name=PROVIDER_NAME
        )

    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for server in servers:
        server_id = str(server["id"])
        ip = utils.primary_ipv4(server) or ""
        instances[server_id] = [
            common.InstanceInfo(
                instance_id=server_id,
                # Bare metal exposes one public management address; there is
                # no separate internal address to report.
                internal_ip=ip,
                external_ip=ip,
                ssh_port=SSH_PORT,
                tags={},
                node_name=str(
                    (server.get("attributes") or {}).get("hostname") or server_id
                ),
            )
        ]
        if head_instance_id is None:
            head_instance_id = server_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name=PROVIDER_NAME,
        # Composed, not read: the server object exposes no ssh user field;
        # the stock OS answers as root.
        ssh_user=SSH_USER,
    )


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Tuple[Optional[status_lib.ClusterStatus], Optional[str]]]:
    del cluster_name, provider_config
    client = _client()
    result: Dict[str, Tuple[Optional[status_lib.ClusterStatus], Optional[str]]] = {}
    for server in client.list_servers(hostname=cluster_name_on_cloud):
        raw = utils.server_status(server)
        sky_status = _STATUS_MAP.get(raw)
        if sky_status is None and raw not in _STATUS_MAP:
            # Unmapped provisioning slug (open set): log it verbatim and
            # treat as in-flight, per the API contract. Never crash the
            # reconcile loop over a slug the platform just invented.
            logger.info(
                "latitude server %s in unmapped provisioning status %r "
                "(treated as in-flight)",
                server.get("id"),
                raw,
            )
        result[str(server["id"])] = (sky_status, None)
    return result


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """No-op: Latitude bare metal has no port firewall in the deploy path
    (OpenPortsVersion.LAUNCH_ONLY; the optional firewall product is not used
    by this lane)."""
    del cluster_name_on_cloud, ports, provider_config


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[int],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """No-op counterpart to open_ports."""
    del cluster_name_on_cloud, ports, provider_config
