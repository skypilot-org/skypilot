"""QuantaCloud instance provisioning.

The lifecycle, and the two QuantaCloud-specific wrinkles that shape it:

* **Deployments carry NO name/label/tag.** The create body takes
  provider/offer/ssh-key-ids only, and the deployment object exposes no
  hostname — so server-side adoption-by-name (the Latitude pattern) does
  not exist. This module keeps a LOCAL mapping
  ``cluster_name_on_cloud -> deployment_id``
  (``~/.sky/quantacloud-clusters.json``, written atomically at the single
  create point). Every identity-bearing operation (query, cluster info,
  terminate) resolves through it and REFUSES to guess when it is missing:
  an unmapped live deployment is someone's box (a console experiment, or a
  lost state file) and the operator decides — never a silent fleet-wide
  teardown.
* **Offer UUIDs are ephemeral.** The catalog's InstanceType is the stable
  ``<gpu-slug>:<count>`` token; at launch this module re-resolves the
  concrete in-stock offer from the LIVE list (``find_offers``) instead of
  trusting a catalog-time UUID — a frozen UUID is the most likely silent
  rot on this provider.

Teardown: stopping a deployment TERMINATES it and deletes its disk (there
is no stop-and-keep), so terminate maps to ``POST /deployments/{id}/stop``
(idempotent: 404 and cannot-stop validation errors are success), and STOP
is declared unsupported at the cloud layer.

The SSH user is read from the LIVE deployment's ``connection.ssh_user``
once ``active`` (the provider reports it per deployment); the documented
value is ``ubuntu`` but the lane treats it as UNMEASURED until the first
funded deploy (the Latitude lesson: documented root, real ubuntu).
"""

from __future__ import annotations

import json
import os
import tempfile
import typing
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.quantacloud import utils
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    pass

logger = sky_logging.init_logger(__name__)

PROVIDER_NAME = 'quantacloud'

# The documented SSH user (deployments.md: ``ssh_user`` is `ubuntu`).
# UNMEASURED until the first funded deploy (ENG-507's docs-said-root
# lesson): the live deployment object's connection.ssh_user is the source
# of truth once active, and this constant is only the fallback.
SSH_USER = 'ubuntu'

# The literal the ray template carries until `configure_ssh_info` substitutes
# the real key into node_config['PublicKey'] (see the check in run_instances
# for why the literal itself must be refused).
_SSH_PUBLIC_KEY_PLACEHOLDER = 'skypilot:ssh_public_key_content'

# QuantaCloud deployment status -> SkyPilot cluster status.
#
# The docs give a closed set, but the Latitude lesson (provisioning slugs
# returned verbatim, open set) says treat it as open anyway: mapped steady
# states only; anything else maps to None = transitional (SkyPilot keeps
# waiting) and is logged verbatim. Terminal states surface as STOPPED
# (the ClusterStatus enum has no TERMINATED; latitude maps its
# failed_deployment the same way) so reconcile reaps them; `terminated`
# rows persist in the provider's list as history and are excluded by
# `_is_live`.
_STATUS_MAP: Dict[str, Optional[status_lib.ClusterStatus]] = {
    'active': status_lib.ClusterStatus.UP,
    'failed': status_lib.ClusterStatus.STOPPED,
    'interrupted': status_lib.ClusterStatus.STOPPED,
    'terminated': status_lib.ClusterStatus.STOPPED,
    'provisioning': None,
    'connecting': None,
    'stopping': None,
}

# The local cluster-identity store (see module docstring): deployments have
# no name field, so cluster_name_on_cloud -> deployment_id lives here.
_STATE_PATH = os.path.expanduser('~/.sky/quantacloud-clusters.json')


def _client() -> 'utils.QuantacloudClient':
    return utils.client_from_env()


def _sky_status(deployment: Dict[str, Any]):
    """Map one deployment's status, refusing to guess at an unknown state."""
    raw = utils.deployment_status(deployment)
    if raw in _STATUS_MAP:
        return _STATUS_MAP[raw]
    # Open-set tolerance: an unknown slug is logged verbatim and treated
    # as in-flight (a new slug must not crash the reconcile loop).
    logger.info(
        'quantacloud deployment %s in unmapped status %r (treated as '
        'in-flight)',
        deployment.get('id'),
        raw,
    )
    return None


def _is_live(deployment: Dict[str, Any]) -> bool:
    """A deployment that still exists on the account in a non-final state.

    `terminated` rows persist in the list as history — they are NOT live.
    `failed`/`interrupted` boxes are terminal but visible (reconcile must
    reap them), and stopping them is an idempotent no-op.
    """
    return utils.deployment_status(deployment) != utils.STATUS_TERMINATED


# -- local cluster-identity store -----------------------------------------
#
# Deployments have no name/label/tag (see module docstring), so the
# cluster identity lives here. Fail-loud rules: a corrupt file raises
# (never silently forgets a billing box); a missing file is an empty map.


def _load_state() -> Dict[str, Dict[str, Any]]:
    try:
        with open(_STATE_PATH, encoding='utf-8') as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise utils.QuantacloudError(
            f'cluster-identity store {_STATE_PATH} is corrupt ({exc}); '
            'refusing to forget live deployments — fix or remove the file '
            'manually after checking the account deployments') from exc
    if not isinstance(state, dict):
        raise utils.QuantacloudError(
            f'cluster-identity store {_STATE_PATH} is not a JSON object; '
            'refusing to forget live deployments')
    return state


def _save_state(state: Dict[str, Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    # Atomic replace: a torn state file must never lose a billing box's id.
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(_STATE_PATH),
                                    prefix='.clusters-',
                                    suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, _STATE_PATH)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _forget(state: Dict[str, Dict[str, Any]],
            cluster_name_on_cloud: str) -> None:
    if cluster_name_on_cloud in state:
        del state[cluster_name_on_cloud]
        _save_state(state)


def _mapped_id(state: Dict[str, Dict[str, Any]],
               cluster_name_on_cloud: str) -> Optional[str]:
    entry = state.get(cluster_name_on_cloud)
    if not isinstance(entry, dict):
        return None
    deployment_id = entry.get('deployment_id')
    return str(deployment_id) if deployment_id else None


# -- provisioning ----------------------------------------------------------


def run_instances(
    region: str,
    cluster_name: str,
    cluster_name_on_cloud: str,
    config: common.ProvisionConfig,
) -> common.ProvisionRecord:
    """Create (or adopt) the cluster's single deployment and wait for active."""
    del cluster_name  # cluster_name_on_cloud is the identity we use
    # pylint: disable=import-outside-toplevel
    from sky.catalog.data_fetchers import fetch_quantacloud

    client = _client()
    state = _load_state()
    deployment_id = _mapped_id(state, cluster_name_on_cloud)

    if deployment_id is not None:
        try:
            deployment = client.get_deployment(deployment_id)
        except utils.QuantacloudNotFoundError:
            # Stale mapping (already terminated server-side): forget it.
            _forget(state, cluster_name_on_cloud)
            deployment_id = None
        else:
            if not _is_live(deployment):
                _forget(state, cluster_name_on_cloud)
                deployment_id = None
            else:
                status = utils.deployment_status(deployment)
                if status in (utils.STATUS_FAILED, utils.STATUS_INTERRUPTED):
                    raise utils.QuantacloudError(
                        f'deployment {deployment_id} for cluster '
                        f'{cluster_name_on_cloud!r} is {status}; terminate it '
                        'before relaunching (not auto-retried — report and '
                        'decide)')
                # Transitional (provisioning/connecting) or active: adopt
                # and wait for `active` below.

    created: List[str] = []
    if deployment_id is None:
        # No mapping: is the account holding someone else's box? A live
        # deployment we cannot attribute is a guess — refuse it (a
        # console experiment or a lost state file; the operator decides).
        live_others = [d for d in client.list_deployments() if _is_live(d)]
        if live_others:
            ids = [str(d.get('id')) for d in live_others]
            raise utils.QuantacloudError(
                f'cluster {cluster_name_on_cloud!r} has no recorded '
                f'deployment, but {len(live_others)} live deployment(s) '
                f'exist on the account ({ids}); refusing to guess which is '
                'ours — terminate the foreign box from the console or '
                'restore the state file, then relaunch')

        node_config = config.node_config
        token = node_config.get('InstanceType')
        if not token:
            raise utils.QuantacloudError(
                'node_config is missing InstanceType (the QuantaCloud '
                '"<gpu-slug>:<count>" catalog token, e.g. '
                'rtx-pro-6000-blackwell:1)')
        gpu_slug, gpu_count = fetch_quantacloud.parse_instance_type_token(
            str(token))

        # node_config['PublicKey'] is the ONE canonical source: backend_utils
        # puts Quantacloud on the generic `auth.configure_ssh_info` path,
        # which substitutes the real key into the template's
        # `skypilot:ssh_public_key_content` placeholder. The placeholder
        # check is the point: if substitution did NOT run, the literal is
        # still TRUTHY, a bare `if not public_key` waves garbage into
        # ensure_ssh_key, and the deployment fails far from the cause (the
        # same trap the Spheron carry documented).
        public_key = (config.node_config or {}).get('PublicKey')
        if isinstance(public_key, str):
            public_key = public_key.strip()
        if not public_key or public_key == _SSH_PUBLIC_KEY_PLACEHOLDER:
            raise utils.QuantacloudError(
                'node_config is missing its PublicKey, or the placeholder '
                f'was never substituted (got {public_key!r}); '
                'auth.configure_ssh_info must run before provisioning — '
                'a QuantaCloud deployment without a real public key is '
                'unreachable')

        # Launch-time offer re-resolution: the catalog token names the
        # (slug, count) pair; the concrete in-stock offer is found on the
        # LIVE list (UUIDs are ephemeral — a frozen one is silent rot).
        offers = client.find_offers(gpu_slug=gpu_slug,
                                    gpu_count=gpu_count,
                                    region=region)
        if not offers:
            raise utils.QuantacloudError(
                f'no in-stock QuantaCloud offer for {gpu_slug!r} x'
                f'{gpu_count} in region {region!r}; the offer behind the '
                'catalog row went out of stock (offer UUIDs are ephemeral) — '
                'fail closed rather than rent a different shape')
        offer = offers[0]  # find_offers sorts by pricePerGpu ascending
        offer_id = str(offer.get('id'))

        ssh_key_id = client.ensure_ssh_key(utils.SSH_KEY_NAME, public_key)
        deployment = client.create_deployment(offer_id=offer_id,
                                              ssh_key_ids=[ssh_key_id])
        deployment_id = str(deployment['id'])
        created = [deployment_id]

    # Readiness is `status == active` (connection info, including the
    # SSH user, is non-null only then); the poll fails closed on
    # failed/interrupted and on the deadline.
    deployment = client.wait_until_active(deployment_id)

    # Record the identity + the provider-reported SSH user for every later
    # operation (query/cluster-info/terminate) — deployments have no name
    # field to find them by (see module docstring).
    state = _load_state()
    state[cluster_name_on_cloud] = {
        'deployment_id': deployment_id,
        'region': region,
        'ssh_user': client.ssh_user(deployment) or SSH_USER,
        'instance_type': str((config.node_config or {}).get('InstanceType') or
                             ''),
    }
    _save_state(state)

    return common.ProvisionRecord(
        provider_name=PROVIDER_NAME,
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=deployment_id,
        resumed_instance_ids=[],
        created_instance_ids=created,
    )


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """No-op: run_instances already blocks until `active`."""
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
    would TypeError at dispatch. Quantacloud declares STOP unsupported in
    Quantacloud._CLOUD_UNSUPPORTED_FEATURES (stopping a deployment
    TERMINATES it and deletes its disk — there is no stop-and-keep), so
    SkyPilot never routes here; if anything ever does, refuse loudly
    rather than destroying the box as a "stop", which deletes the disk
    while the caller believed it was preserved.
    """
    del cluster_name_on_cloud, provider_config, worker_only
    raise NotImplementedError(
        'stop is unsupported on QuantaCloud: stopping a deployment '
        'terminates it and deletes its disk. Use terminate_instances for '
        'teardown.')


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Terminate the cluster's deployment (see module docstring)."""
    del provider_config, worker_only  # single-node: head is the cluster
    if not str(cluster_name_on_cloud or '').strip():
        # An empty identity maps to NOTHING here — but the refusal stays
        # loud so a miswired caller can never destroy by guess (CodeRabbit
        # on the Latitude PR, same class).
        raise utils.QuantacloudError(
            'refusing to terminate with an empty cluster_name_on_cloud')
    client = _client()
    state = _load_state()
    deployment_id = _mapped_id(state, cluster_name_on_cloud)
    if deployment_id is None:
        # Unmapped: an identity we cannot resolve must never destroy by
        # guess. Report the account's live deployments for the operator.
        live = [
            str(d.get('id')) for d in client.list_deployments() if _is_live(d)
        ]
        if not live:
            return  # nothing anywhere: already torn down
        raise utils.QuantacloudError(
            f'no recorded deployment for cluster {cluster_name_on_cloud!r}; '
            f'live deployments on the account: {live}. refusing to guess '
            'which is ours — operator decides (console teardown or '
            'state-file restore)')
    # stop_deployment is idempotent (404 / cannot-stop validation errors
    # are success inside the client).
    client.stop_deployment(deployment_id)
    _forget(state, cluster_name_on_cloud)
    logger.info('quantacloud deployment %s stopped (terminated)', deployment_id)


def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    del region, provider_config
    client = _client()
    state = _load_state()
    deployment_id = _mapped_id(state, cluster_name_on_cloud)
    if deployment_id is None:
        return common.ClusterInfo(instances={},
                                  head_instance_id=None,
                                  provider_name=PROVIDER_NAME)
    try:
        deployment = client.get_deployment(deployment_id)
    except utils.QuantacloudNotFoundError:
        return common.ClusterInfo(instances={},
                                  head_instance_id=None,
                                  provider_name=PROVIDER_NAME)
    if not _is_live(deployment):
        return common.ClusterInfo(instances={},
                                  head_instance_id=None,
                                  provider_name=PROVIDER_NAME)

    conn = client.connection(deployment) or {}
    ip = str(conn.get('public_ip') or '')
    ssh_port = int(conn.get('ssh_port') or 22)
    # The provider reports the SSH user per deployment once active; the
    # stored value (recorded at create) is the fallback, the documented
    # constant the last resort (docs say `ubuntu`, UNMEASURED until the
    # first funded deploy — the Latitude lesson).
    stored = (state.get(cluster_name_on_cloud) or {}).get('ssh_user')
    ssh_user = (client.ssh_user(deployment) or
                (str(stored) if stored else None) or SSH_USER)
    return common.ClusterInfo(
        instances={
            deployment_id: [
                common.InstanceInfo(
                    instance_id=deployment_id,
                    # A QuantaCloud VM exposes one public address; there is
                    # no separate internal address to report.
                    internal_ip=ip,
                    external_ip=ip,
                    ssh_port=ssh_port,
                    tags={},
                    node_name=deployment_id,
                )
            ]
        },
        head_instance_id=deployment_id,
        provider_name=PROVIDER_NAME,
        ssh_user=ssh_user,
    )


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Tuple[Optional[status_lib.ClusterStatus], Optional[str]]]:
    del cluster_name, provider_config, non_terminated_only
    client = _client()
    state = _load_state()
    deployment_id = _mapped_id(state, cluster_name_on_cloud)
    if deployment_id is None:
        return {}
    try:
        deployment = client.get_deployment(deployment_id)
    except utils.QuantacloudNotFoundError:
        # Gone server-side: forget the mapping so nothing resurrects it,
        # and report absence — no instances for this cluster (the same
        # signal a Latitude server list gives when the box was deleted).
        _forget(state, cluster_name_on_cloud)
        return {}
    return {deployment_id: (_sky_status(deployment), None)}


def open_ports(
    cluster_name_on_cloud: str,
    ports: list,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """No-op: QuantaCloud deployments have no port firewall in the deploy path
    (direct SSH on port 22, documented no egress/ingress charges)."""
    del cluster_name_on_cloud, ports, provider_config


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: list,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """No-op counterpart to open_ports."""
    del cluster_name_on_cloud, ports, provider_config
