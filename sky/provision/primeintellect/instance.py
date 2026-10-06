"""Prime Intellect instance provisioning.

The lifecycle, and the Prime Intellect-specific wrinkles that shape it:

* **Pods carry NAMES.** The create body takes a free-form ``name``, so —
  unlike QuantaCloud — server-side adoption-by-name exists: this module
  names the lane's single pod ``<cluster_name_on_cloud>-head`` and every
  identity-bearing operation (query, cluster info, terminate) filters by
  that exact name. An unmapped-but-live pod with a DIFFERENT name is
  someone's box (a console experiment, another lane) and is reported,
  never adopted.
* **Offers are a live market.** The catalog's InstanceType is the stable
  ``<provider>:<gpuType>:<count>`` token; at launch this module
  re-resolves the concrete in-stock offer from the LIVE availability list
  (``find_offers``, VM-class upstreams only) instead of trusting a
  catalog-time snapshot — and the create body echoes that live row
  verbatim (cloudId/gpuType/socket/gpuCount/dataCenterId/country/security
  are a SET; mixing rows is a wrong-region box at best).
* **No stop endpoint exists.** DELETE is the only teardown (404 is
  success); the pod's local disk does not survive. STOP is declared
  unsupported at the cloud layer and refused loudly here.
* **Provisioning fails CLOSED.** ``ERROR`` (with the provider's own
  ``installationFailure``), ``TERMINATED``/``DELETING`` mid-wait (the
  wallet-auto-delete path), and the deadline all raise — never an
  endless wait (the failure mode that kept a Vast instance
  SkyPilotProvisioning for 30+ minutes).

The SSH user is read from the LIVE pod's ``sshConnection`` once ACTIVE
(the provider reports it per pod; the docs example says ``root@... -p
22``) — UNMEASURED until the first funded deploy (ENG-507's lesson:
documented root, real ubuntu). The provision poll floor is 10s.
"""

from __future__ import annotations

import typing
from typing import Any, Dict, List, Optional

from sky import exceptions
from sky import sky_logging
from sky.provision import common
from sky.provision.primeintellect import utils
from sky.utils import status_lib
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    pass

logger = sky_logging.init_logger(__name__)

PROVIDER_NAME = 'primeintellect'

# The documented SSH user (docs example: ``root@<ip> -p 22`` on
# primecompute). UNMEASURED until the first funded deploy (the Latitude
# lesson: documented root, real ubuntu): the live pod's sshConnection is
# the source of truth once ACTIVE, and this constant is only the
# fallback for the initial ray bootstrap SSH.
SSH_USER = 'root'

# The literal the ray template carries until `configure_ssh_info`
# substitutes the real key into node_config['PublicKey'] (see the check
# in run_instances for why the literal itself must be refused).
_SSH_PUBLIC_KEY_PLACEHOLDER = 'skypilot:ssh_public_key_content'

# The pod names this module owns (single-node lane: the head IS the
# cluster; MULTI_NODE is declared unsupported at the cloud layer).
def _head_name(cluster_name_on_cloud: str) -> str:
    return f'{cluster_name_on_cloud}-head'


def _owned_names(cluster_name_on_cloud: str) -> List[str]:
    return [_head_name(cluster_name_on_cloud),
            f'{cluster_name_on_cloud}-worker']

# Pod status -> SkyPilot cluster status. Mapped steady states only;
# anything else maps to None = transitional (SkyPilot keeps waiting) and
# is logged verbatim (the Latitude lesson: provisioning slugs are an
# open set — do not crash the reconcile loop on a new one). Terminal
# states surface as STOPPED (the ClusterStatus enum has no TERMINATED)
# so reconcile reaps them.
_STATUS_MAP: Dict[str, Optional[status_lib.ClusterStatus]] = {
    'ACTIVE': status_lib.ClusterStatus.UP,
    'ERROR': status_lib.ClusterStatus.STOPPED,
    'STOPPED': status_lib.ClusterStatus.STOPPED,
    'DELETING': None,   # being deleted — excluded once it settles
    'TERMINATED': None,  # already terminated — excluded
    'UNKNOWN': None,    # provider gave up classifying — excluded
    'PROVISIONING': None,
    'PENDING': None,
}


def _client() -> 'utils.PrimeIntellectClient':
    return utils.client_from_env()


def _sky_status(pod: Dict[str, Any]):
    """Map one pod's status, refusing to guess at an unknown state."""
    raw = utils.pod_status(pod)
    if raw in _STATUS_MAP:
        return _STATUS_MAP[raw]
    # Open-set tolerance: an unknown slug is logged verbatim and treated
    # as in-flight (a new slug must not crash the reconcile loop).
    logger.info('primeintellect pod %s in unmapped status %r (treated as '
                'in-flight)', pod.get('id'), raw)
    return None


def _is_live(pod: Dict[str, Any]) -> bool:
    """A pod that still exists on the account in a non-final state."""
    return utils.pod_status(pod) not in (
        utils.STATUS_TERMINATED, utils.STATUS_DELETING)


def _named_pods(client: 'utils.PrimeIntellectClient',
                cluster_name_on_cloud: str,
                statuses: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """The cluster's pods by exact name (with optional status filter)."""
    names = set(_owned_names(cluster_name_on_cloud))
    found: Dict[str, Dict[str, Any]] = {}
    for pod in client.list_pods():
        if str(pod.get('name') or '') not in names:
            continue
        if statuses is not None and utils.pod_status(pod) not in statuses:
            continue
        found[str(pod['id'])] = pod
    return found


def _conn_str(pod: Dict[str, Any]) -> Optional[str]:
    """The pod's sshConnection as a string (the schema allows a list of
    strings/nulls; flatten to the first usable entry)."""
    raw = pod.get('sshConnection')
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                return entry
    return None

# -- provisioning ----------------------------------------------------------


def run_instances(
    region: str,
    cluster_name: str,
    cluster_name_on_cloud: str,
    config: common.ProvisionConfig,
) -> common.ProvisionRecord:
    """Create (or adopt) the cluster's single named pod and wait for ACTIVE."""
    del cluster_name  # cluster_name_on_cloud is the identity we use
    # pylint: disable=import-outside-toplevel
    from sky.catalog.data_fetchers import fetch_primeintellect

    if config.count != 1:
        raise utils.PrimeintellectError(
            f'Prime Intellect lane is single-node; config.count='
            f'{config.count} (MULTI_NODE is declared unsupported at the '
            'cloud layer)')

    client = _client()
    head_name = _head_name(cluster_name_on_cloud)
    owned = _named_pods(client, cluster_name_on_cloud)
    # Adoption considers LIVE pods only: a TERMINATED/DELETING pod with
    # our name is history, not an endpoint — adopting it would wait on a
    # box that is already gone.
    live_owned = {pod_id: pod for pod_id, pod in owned.items()
                  if _is_live(pod)}

    # Adoption: a live pod with our exact name. ERROR is a steady state
    # that can never reach ready — fail fast rather than wait on it (the
    # playbook's adoption rule).
    for pod_id, pod in live_owned.items():
        status = utils.pod_status(pod)
        if status == utils.STATUS_ERROR:
            failure = str(pod.get('installationFailure') or '')
            raise utils.PrimeintellectError(
                f'pod {pod_id} (name {pod.get("name")!r}) is ERROR'
                f' ({failure}); terminate it before relaunching — not '
                'auto-retried (report and decide)')
        # PROVISIONING/PENDING/ACTIVE: adopt and wait below.

    created: List[str] = []
    head_instance_id: Optional[str] = None
    if not live_owned:
        # No pod with our name: is the account holding someone else's
        # box? A live pod we cannot attribute is a guess — refuse it (a
        # console experiment, another lane; the operator decides).
        live_others = [p for p in client.list_pods() if _is_live(p)]
        if live_others:
            names = [f'{p.get("name")!r} ({p.get("id")})' for p in live_others]
            raise utils.PrimeintellectError(
                f'cluster {cluster_name_on_cloud!r} has no pod, but '
                f'{len(live_others)} live pod(s) exist on the account: '
                f'{", ".join(names)}; refusing to guess — terminate the '
                'foreign box from the console or name it to this cluster, '
                'then relaunch')

        node_config = config.node_config or {}
        token = node_config.get('InstanceType')
        if not token:
            raise utils.PrimeintellectError(
                'node_config is missing InstanceType (the Prime Intellect '
                '"<provider>:<gpuType>:<count>" catalog token, e.g. '
                'dc_gnu:RTX_PRO_6000B_96GB:1)')
        _, gpu_type, gpu_count = (
            fetch_primeintellect.parse_instance_type_token(str(token)))

        # node_config['PublicKey'] is the ONE canonical source:
        # backend_utils routes PrimeIntellect through
        # setup_primeintellect_authentication, which registers the key on
        # the account and `configure_ssh_info` substitutes the real key
        # into the template's `skypilot:ssh_public_key_content`
        # placeholder. The placeholder check is the point: if substitution
        # did NOT run, the literal is still TRUTHY, a bare
        # `if not public_key` waves garbage into ensure_ssh_key, and the
        # pod fails far from the cause.
        public_key = node_config.get('PublicKey')
        if isinstance(public_key, str):
            public_key = public_key.strip()
        if not public_key or public_key == _SSH_PUBLIC_KEY_PLACEHOLDER:
            raise utils.PrimeintellectError(
                'node_config is missing its PublicKey, or the placeholder '
                f'was never substituted (got {public_key!r}); '
                'auth.configure_ssh_info must run before provisioning — '
                'a Prime Intellect pod without a real public key is '
                'unreachable')

        # Keys are ACCOUNT-level on this API and injected at deploy time;
        # the auth hook registers ours, and this idempotent re-check makes
        # the launch self-sufficient (a pod without our key is
        # unreachable).
        client.ensure_ssh_key(utils.SSH_KEY_NAME, public_key)

        # Launch-time offer re-resolution: the catalog token names the
        # (provider, gpuType, count) triple; the concrete in-stock offer
        # is found on the LIVE list (offers are a live market — a frozen
        # cloudId is silent rot). `region` is the dataCenter token — the
        # same token the catalog row's Region column carries and the
        # create body's dataCenterId takes.
        offers = client.find_offers(gpu_type=gpu_type,
                                    gpu_count=gpu_count,
                                    data_center=region,
                                    vm_class_only=True)
        if not offers:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(
                    f'no in-stock Prime Intellect offer for {gpu_type!r} '
                    f'x{gpu_count} in dataCenter {region!r} (VM-class '
                    'upstreams only); the offer behind the catalog row went '
                    'out of stock — fail closed rather than rent a '
                    'different shape')
        offer = offers[0]  # find_offers sorts by per-GPU price ascending
        try:
            pod = client.create_pod(name=head_name, offer=offer)
        except utils.PrimeintellectResourcesUnavailableError as exc:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(
                    f'Prime Intellect refused the create for {gpu_type!r} '
                    f'x{gpu_count} in {region!r}: {exc}') from exc
        head_instance_id = str(pod['id'])
        created = [head_instance_id]
        logger.info('Launched primeintellect pod %s (name %r, offer '
                    'cloudId %s, provider %s).', head_instance_id,
                    head_name, offer.get('cloudId'), offer.get('provider'))
    else:
        head_instance_id = next(iter(live_owned))
        logger.info('Adopting existing primeintellect pod %s (name %r).',
                    head_instance_id, live_owned[head_instance_id].get('name'))

    assert head_instance_id is not None

    # Readiness is `status == ACTIVE` (sshConnection is non-null only
    # then); the poll fails closed on ERROR, on TERMINATED/DELETING
    # (the wallet-auto-delete path), and on the deadline.
    client.wait_until_active(head_instance_id)

    return common.ProvisionRecord(
        provider_name=PROVIDER_NAME,
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=head_instance_id,
        resumed_instance_ids=[],
        created_instance_ids=created,
    )


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """No-op: run_instances already blocks until ACTIVE."""
    del region, cluster_name_on_cloud, state


def stop_instances(
    region: str,
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """STOP is refused — there is no stop endpoint at all.

    The dispatch contract (sky/provision/__init__.py stop_instances
    wrapper after `provider_name` is stripped) is this signature — a
    copied `(region, cluster_name, ...)` shape does not BIND and would
    TypeError at dispatch. PrimeIntellect declares STOP unsupported in
    PrimeIntellect._CLOUD_UNSUPPORTED_FEATURES (no stop endpoint exists;
    DELETE is the only teardown and the pod's disk does not survive), so
    SkyPilot never routes here; if anything ever does, refuse loudly
    rather than destroying the box as a "stop".
    """
    del region, cluster_name, cluster_name_on_cloud, provider_config
    del worker_only
    raise NotImplementedError(
        'stop is unsupported on Prime Intellect: there is no stop '
        'endpoint — DELETE (terminate_instances) is the only teardown '
        'and nothing on the pod\'s disk survives it.')


def terminate_instances(
    region: str,
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Terminate the cluster's named pods (DELETE; 404 is success)."""
    del region, cluster_name, provider_config, worker_only
    if not str(cluster_name_on_cloud or '').strip():
        # An empty identity would match NOTHING by name here — but the
        # refusal stays loud so a miswired caller can never destroy by
        # guess (the Latitude/QuantaCloud rule).
        raise utils.PrimeintellectError(
            'refusing to terminate with an empty cluster_name_on_cloud')

    client = _client()
    owned = _named_pods(client, cluster_name_on_cloud)
    live = {pod_id: pod for pod_id, pod in owned.items() if _is_live(pod)}
    if not live:
        # Nothing with our name anywhere: already torn down (a pod list
        # with zero matches must NOT fall through to a fleet-wide delete —
        # and this path cannot: every delete below is keyed by name).
        logger.info('No live primeintellect pods for cluster %r — '
                    'already terminated.', cluster_name_on_cloud)
        return

    for pod_id, pod in live.items():
        client.delete_pod(pod_id)
        logger.info('Terminated primeintellect pod %s (name %r).', pod_id,
                    pod.get('name'))

def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    """The cluster's ACTIVE pod as ClusterInfo (SSH endpoint + user)."""
    del region, provider_config
    client = _client()
    owned = _named_pods(client, cluster_name_on_cloud, statuses=['ACTIVE'])
    if not owned:
        return common.ClusterInfo(instances={},
                                  head_instance_id=None,
                                  provider_name=PROVIDER_NAME)
    head_instance_id = next(iter(owned))
    pod = owned[head_instance_id]

    # sshConnection can lag ACTIVE by a beat; a short bounded retry keeps
    # the first cluster-info call after readiness from failing the launch.
    # (The client's per-call timeout already bounds every HTTP hop.)
    conn = _conn_str(pod)
    tries = 6
    while conn is None and tries > 0:
        logger.info('sshConnection not ready for pod %s; retrying...',
                    head_instance_id)
        pod = client.get_pod(head_instance_id)
        conn = _conn_str(pod)
        tries -= 1
    if conn is None:
        raise utils.PrimeintellectError(
            f'pod {head_instance_id} is ACTIVE but sshConnection never '
            'populated; refusing to guess an endpoint — check the pod in '
            'the console')

    user, _ = utils.parse_ssh_connection(conn)
    ssh_user = user or SSH_USER
    ip = utils.pod_ip(pod)
    if not ip:
        raise utils.PrimeintellectError(
            f'pod {head_instance_id} is ACTIVE but carries no ip; refusing '
            'to guess an endpoint')
    port = utils.ssh_port(pod)
    return common.ClusterInfo(
        instances={
            head_instance_id: [
                common.InstanceInfo(
                    instance_id=head_instance_id,
                    # A Prime Intellect VM exposes one public address; no
                    # separate internal address to report.
                    internal_ip=ip,
                    external_ip=ip,
                    ssh_port=port,
                    tags={'provider': str(pod.get('providerType') or '')},
                    node_name=head_instance_id,
                )
            ]
        },
        head_instance_id=head_instance_id,
        provider_name=PROVIDER_NAME,
        ssh_user=ssh_user,
    )


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional[status_lib.ClusterStatus], Optional[str]]]:
    """See sky/provision/__init__.py"""
    del cluster_name, provider_config, retry_if_missing
    client = _client()
    owned = _named_pods(client, cluster_name_on_cloud)
    statuses: Dict[str, Tuple[Optional[status_lib.ClusterStatus],
                              Optional[str]]] = {}
    for pod_id, pod in owned.items():
        status = _sky_status(pod)
        if non_terminated_only and status is None:
            continue
        statuses[pod_id] = (status, None)
    return statuses


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """No-op: Prime Intellect VMs expose their ports directly (the docs'
    port-mapping table lists the open set; the lane only needs 22)."""
    del cluster_name_on_cloud, ports, provider_config


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """No-op counterpart to open_ports."""
    del cluster_name_on_cloud, ports, provider_config
