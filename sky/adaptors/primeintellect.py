"""Prime Intellect REST client.

Prime Intellect (https://primeintellect.ai) is a GPU BROKER: one API key
rents on-demand instances ("pods") across ~25 upstream compute providers
(Prime's own ``dc_*`` datacenters, massedcompute, hyperstack, runpod, ...).
An availability row is one deployable configuration — upstream ``provider``
x ``gpuType`` x ``socket`` x ``gpuCount`` x ``dataCenter`` — and the create
body echoes that row verbatim (``cloudId``/``gpuType``/``socket``/
``gpuCount``/``dataCenterId``/``country``/``security``/image).

Wire-protocol notes that differ from the sibling brokers (each verified
against https://api.primeintellect.ai/openapi.json on 2026-10-06):

* **Plain JSON.** Two error shapes, both preserved verbatim into the raised
  error: 422 validation carries ``{"errors": [{"param", "details"}]}``;
  auth failures carry FastAPI's ``{"detail": ...}`` (401 invalid token,
  403 not authenticated). Flattening either to a status line is the bug
  class that made a Spheron stockout read as an auth failure.
* **``/availability/gpus`` intermittently serves 200-EMPTY responses off a
  flaky replica** (observed 2026-10-06: a query that returned rows
  seconds earlier returned ``{"items": [], "totalCount": 0}``, and the
  same query repopulated on retry). An empty fetch is therefore retried a
  bounded number of times before it is allowed to mean anything — and
  even then the FETCHER cross-checks ``/availability/gpu-summary`` before
  writing zero-stock.
* **Offers are a live market.** The same probe watched the RTX PRO 6000
  list flip 2 offers -> 0 -> 2 within minutes. The launch path re-resolves
  the concrete offer from the LIVE list (``find_offers``) rather than
  trusting a catalog-time snapshot; ``cloudId`` values are upstream plan
  tokens, not stable ids.
* **The deployment model is PER-UPSTREAM-PROVIDER.** Prime's own
  ``dc_*`` datacenters and ``primecompute`` boot full-OS VMs (root SSH,
  ``ubuntu_22_cuda_12`` / ``ubuntu_26`` images); container-shaped upstreams
  (notably runpod) cannot run a VM bootstrap ladder. This client carries a
  VM-CLASS UPSTREAM ALLOWLIST (``is_vm_class_upstream``) and every
  offer-filtering path applies it.
* **Funding is a precondition, not capacity.** Credits are deducted per
  minute and a mid-flight $0 wallet AUTO-DELETES the running pod with no
  grace period. The wallet is checked by callers, never probed with
  creates.
* **No stop endpoint exists** — DELETE is the only teardown (404 is
  success, already gone); nothing on the pod's local disk survives.
* **Collection paths use a trailing slash** (``/pods/``, ``/ssh_keys/``);
  ``/availability/*`` does not. Kept verbatim.

Every call carries an explicit timeout (a hung HTTP call once wedged a
runner for 30+ minutes), and non-JSON or non-dict payloads raise typed
errors rather than leaking AttributeError/UnicodeDecodeError. There is no
documented rate limit and no ``X-RateLimit-*`` response header (probed
2026-10-06): 429s back off exponentially, bounded.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

API_BASE = 'https://api.primeintellect.ai/api/v1'

# Identifies the integration to the provider's logs.
USER_AGENT = 'urun-skypilot-primeintellect/0.1 (+https://urun.sh)'

# One timeout for every request, including the reads inside poll loops —
# the point is the bound, not the value.
DEFAULT_TIMEOUT_S = 60

# No documented rate limit (no X-RateLimit-* headers, probed 2026-10-06);
# bounded exponential backoff, three strikes.
MAX_RATE_LIMIT_RETRIES = 3
MAX_RETRY_AFTER_S = 60.0

# The flaky-replica empty-200 (see module docstring): an empty availability
# fetch is retried this many times before the caller may interpret it.
EMPTY_FETCH_RETRIES = 4
EMPTY_FETCH_BACKOFF_S = 2.5

# Pagination: /availability/gpus uses page/page_size (1-indexed, max 100,
# response {items, totalCount}); /pods/ uses offset/limit. The page cap
# turns a server that ignores pagination into a loud error instead of a
# silently truncated catalog.
PAGE_SIZE = 100
MAX_PAGES = 100

# Pods: documented steady/transitional states (PodStatusEnum in the
# OpenAPI spec). PROVISIONING/PENDING are transitional; ERROR is terminal
# failure (installationFailure carries the reason); DELETING/TERMINATED
# are gone from the live list's perspective; STOPPED exists only for
# pause-capable upstreams (RunPod) and is not part of this lane's path.
STATUS_PROVISIONING = 'PROVISIONING'
STATUS_PENDING = 'PENDING'
STATUS_ACTIVE = 'ACTIVE'
STATUS_STOPPED = 'STOPPED'
STATUS_ERROR = 'ERROR'
STATUS_DELETING = 'DELETING'
STATUS_UNKNOWN = 'UNKNOWN'
STATUS_TERMINATED = 'TERMINATED'

# The VM-class upstream allowlist: Prime's own datacenters boot full-OS VMs
# (root SSH, systemd, the SkyPilot bootstrap ladder); third-party upstreams
# vary (runpod is container-shaped) and are admitted only with a measured
# receipt per upstream (the playbook's per-provider deployment-model rule).
_VM_CLASS_UPSTREAMS = frozenset({'primecompute', 'primeintellect'})
_VM_CLASS_UPSTREAM_PREFIXES = ('dc_',)


def is_vm_class_upstream(provider: str) -> bool:
    """Whether an upstream provider boots full-OS VMs (our lane's shape).

    Prime's own ``dc_*`` datacenters and ``primecompute`` do; everything
    else is unmeasured and excluded until a receipt admits it.
    """
    name = str(provider or '').strip()
    return (name in _VM_CLASS_UPSTREAMS or
            name.startswith(_VM_CLASS_UPSTREAM_PREFIXES))


# The image the lane prefers on every VM-class offer (Ubuntu 22.04 +
# NVIDIA drivers, CUDA 12 preinstalled; the HOST driver version is
# undocumented — nvidia-smi on a real box is the only answer).
PREFERRED_IMAGE = 'ubuntu_22_cuda_12'

# Environment variable the callers read the key from (the task secret
# channel's spelling; the user's shell uses the same one — there is no
# vendor env convention and no alias to normalize).
API_KEY_ENV = 'PRIME_INTELLECT_API_KEY'
API_KEY_FILE = '~/.prime-intellect/credentials'

# The poll cadence floor: the docs give no documented rate budget, so keep
# the controller's own watcher discipline (60s) and the provision poll at
# 10-15s; faster polling is waste on a broker with a flaky replica.
MIN_POLL_INTERVAL_S = 10.0

# ~10-minute worst-case provisioning (the docs say instances "typically
# launch within a few minutes"; a stuck PROVISIONING fails closed at the
# deadline, never an endless wait).
DEFAULT_PROVISION_TIMEOUT_S = 20 * 60


class PrimeintellectError(RuntimeError):
    """Any Prime Intellect API failure. Never raised with the API key in it.

    Carries the HTTP status and the provider's own error body when the
    response had one, so callers can branch on validation-vs-auth-vs-stock
    instead of parsing strings (the shadeform INSUFFICIENT_FUNDS lesson).
    """

    def __init__(self, message: str, *, code: str = '',
                 status_code: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class PrimeintellectAuthError(PrimeintellectError):
    """401/403 — key missing, invalid, revoked, or permissions too narrow.

    A 401/403 must never be interpreted as "no capacity": rotate the key at
    https://app.primeintellect.ai/dashboard/tokens (check the Instances
    Read and write + Availability Read permissions for the lane).
    """


class PrimeintellectNotFoundError(PrimeintellectError):
    """404 — the resource is gone. Terminate paths treat this as success."""


class PrimeintellectRateLimited(PrimeintellectError):
    """429 that exhausted its bounded retries."""


class PrimeintellectValidationError(PrimeintellectError):
    """422 — the request body failed validation.

    Carries the provider's own ``errors[]`` (param/details) verbatim in
    ``fields`` so a wrong cloudId/gpuType is diagnosable from the raise.
    """

    def __init__(self, message: str, *,
                 fields: Optional[List[Tuple[str, str]]] = None,
                 status_code: Optional[int] = None):
        super().__init__(message, code='validation_error',
                         status_code=status_code)
        self.fields = fields or []


class PrimeintellectResourcesUnavailableError(PrimeintellectError):
    """Capacity-shaped failure: no in-stock offer matching the request, or
    the create was refused for stock/availability reasons.

    Capacity, not config: a retry with a fresh re-resolution can resolve
    it (offers are a live market). Contrast auth (rotate the key) and
    validation (fix the body).
    """


def _error_fields(raw: bytes) -> List[Tuple[str, str]]:
    """The 422 envelope's (param, details) pairs, or [] if unusable."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            errors = parsed.get('errors')
            if isinstance(errors, list):
                pairs = []
                for entry in errors:
                    if isinstance(entry, dict):
                        pairs.append((str(entry.get('param') or ''),
                                      str(entry.get('details') or '')))
                return pairs
    except (ValueError, AttributeError):
        pass
    return []


def _error_detail(raw: bytes) -> str:
    """The FastAPI ``{"detail": ...}`` message, or '' if unusable."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            detail = parsed.get('detail')
            if isinstance(detail, str):
                return detail[:300]
    except (ValueError, AttributeError):
        pass
    return ''

def parse_ssh_connection(ssh_connection: Any) -> Tuple[Optional[str], int]:
    """Extract (user, port) from a pod's ``sshConnection`` string.

    The provider returns a ready-made connection string (docs example:
    ``root@135.23.125.123 -p 22``); tolerate extra flags and various
    tokenizations (kept from the upstream carry — its parser was the one
    piece worth keeping verbatim). The SSH USER it reports is
    UNMEASURED on a real box until the first funded deploy (the Latitude
    lesson: documented root, real ubuntu) — measure before trusting.
    """
    if isinstance(ssh_connection, list):
        # Schema: items anyOf [string, null] — first usable entry wins.
        ssh_connection = next(
            (entry for entry in ssh_connection
             if isinstance(entry, str) and entry.strip()), None)
    if not isinstance(ssh_connection, str) or not ssh_connection.strip():
        return None, 22
    tokens = ssh_connection.replace('=', ' ').split()
    user: Optional[str] = None
    port = 22
    for i, token in enumerate(tokens):
        if token.startswith('-p') and token[2:].isdigit():
            port = int(token[2:])
        elif token == '-p' and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            port = int(tokens[i + 1])
        elif '@' in token and not token.startswith('-'):
            candidate = token.rsplit('@', 1)[0]
            if candidate and all(c.isalnum() or c in '-_' for c in candidate):
                user = candidate
    return user, port


class PrimeIntellectClient:
    """Thin, explicit client. One method per endpoint we actually use.

    ``transport`` is injected in tests. Signature:
    ``(method, url, headers, payload) -> (status, bytes)``.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = API_BASE,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        transport: Optional[Callable[
            [str, str, Dict[str, str], Optional[bytes]], Any]] = None,
    ):
        if not api_key or not api_key.strip():
            raise PrimeintellectAuthError(
                'Prime Intellect API key is empty; refusing to make '
                'unauthenticated calls that would read as "no capacity"')
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip('/')
        self._timeout_s = timeout_s
        self._transport = transport

    # -- plumbing ---------------------------------------------------------

    def _http(self, method: str, url: str, headers: Dict[str, str],
              payload: Optional[bytes]):
        req = urllib.request.Request(url,
                                     data=payload,
                                     headers=headers,
                                     method=method)
        # No query in errors: no key leakage (the query can carry a token).
        path = url.split('?')[0]
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                # resp.read() runs OUTSIDE urlopen's URLError handling: a
                # stalled body raises TimeoutError/OSError directly and
                # would escape every caller that only catches
                # PrimeintellectError (the trap CodeRabbit flagged on the
                # Spheron client).
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read()
            except (TimeoutError, OSError) as read_exc:
                raise PrimeintellectError(
                    f'{method} {path} failed reading the error '
                    f'body: {read_exc}') from read_exc
        except urllib.error.URLError as exc:  # pragma: no cover - network
            raise PrimeintellectError(
                f'{method} {path} failed: {exc.reason}') from exc
        except (TimeoutError, OSError) as exc:
            raise PrimeintellectError(f'{method} {path} failed: {exc}') from exc

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f'{self._base_url}{path}'
        if params:
            url = f'{url}?{urllib.parse.urlencode(params, doseq=True)}'
        headers = {
            'User-Agent': USER_AGENT,
            'Accept': 'application/json',
            'Authorization': f'Bearer {self._api_key}',
        }
        payload = None
        if body is not None:
            payload = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            if self._transport is not None:
                status, raw = self._transport(method, url, headers, payload)
            else:
                status, raw = self._http(method, url, headers, payload)
            if status == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                # No documented retry hint: bounded exponential.
                time.sleep(min(2.0 * (attempt + 1), MAX_RETRY_AFTER_S))
                continue
            return self._interpret(status, raw, method, path)

        # Unreachable: the loop returns or raises on the final attempt.
        raise AssertionError('rate-limit retry loop exhausted')

    def _interpret(self, status: int, raw: bytes, method: str, path: str):
        # Never include the URL query or headers in an error: no key
        # leakage. `path` carries no query by construction.
        where = f'{method} {path}'
        if status < 400:
            # The empty-body success path lives UNDER the status check: a
            # 4xx/5xx with an empty body is a FAILURE, never a silent None
            # — the bug class where a provider's empty-body error would
            # read as "done" while the box keeps billing.
            if not raw:
                return None
            try:
                return json.loads(raw)
            # ValueError covers JSONDecodeError AND UnicodeDecodeError (a
            # non-UTF-8 gateway page is a ValueError), so no raw exception
            # escapes a PrimeintellectError-only caller.
            except ValueError as exc:
                raise PrimeintellectError(
                    f'{where}: response was not JSON ({exc})',
                    status_code=status) from exc

        if status in (401, 403):
            detail = _error_detail(raw) or raw[:200].decode('utf-8', 'replace')
            raise PrimeintellectAuthError(
                f'{where}: HTTP {status} {detail}. Check {API_KEY_ENV} (or '
                'rotate the key at '
                'https://app.primeintellect.ai/dashboard/tokens — the lane '
                'needs Instances Read and write + Availability Read).',
                status_code=status)
        if status == 404:
            raise PrimeintellectNotFoundError(
                f'{where}: not found', status_code=status)
        if status == 422:
            fields = _error_fields(raw)
            rendered = '; '.join(f'{p}: {d}'.strip(' :')
                                 for p, d in fields if p or d)
            if not rendered:
                rendered = raw[:200].decode('utf-8', 'replace')
            raise PrimeintellectValidationError(
                f'{where}: HTTP 422 {rendered}',
                fields=fields,
                status_code=status)
        if status == 429:
            raise PrimeintellectRateLimited(
                f'{where}: rate limited (no documented budget; bounded '
                'exponential backoff already ran)',
                status_code=status)
        # Capacity-shaped refusals (observed shapes: a create against a
        # sold-out cloudId) must stay distinguishable from config errors.
        body_text = ''
        try:
            body_text = raw[:300].decode('utf-8', 'replace')
        except Exception:  # pragma: no cover - decode('replace') never raises
            pass
        marker = body_text.lower()
        if status in (400, 402, 409, 503) and any(
                word in marker for word in
                ('stock', 'availab', 'capacity', 'unavailable', 'insufficient',
                 'balance', 'credit')):
            kind = ('insufficient funds/credits — a CONFIG error, not '
                    'capacity: top up at '
                    'https://app.primeintellect.ai/dashboard/billing'
                    if any(w in marker for w in
                           ('insufficient', 'balance', 'credit'))
                    else 'resources unavailable')
            raise PrimeintellectResourcesUnavailableError(
                f'{where}: HTTP {status} {kind}: {body_text[:200]}',
                status_code=status)
        raise PrimeintellectError(
            f'{where}: HTTP {status}: {body_text[:200]}',
            status_code=status)

    # -- account ----------------------------------------------------------

    def get_whoami(self) -> Dict[str, Any]:
        payload = self._call('GET', '/user/whoami') or {}
        if not isinstance(payload, dict):
            raise PrimeintellectError('GET /user/whoami: expected an object,'
                                      f' got {type(payload).__name__}')
        return payload

    def get_wallet(self, *, team_id: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if team_id:
            params['teamId'] = team_id
        payload = self._call('GET', '/billing/wallet', params=params) or {}
        if not isinstance(payload, dict):
            raise PrimeintellectError('GET /billing/wallet: expected an '
                                      f'object, got {type(payload).__name__}')
        return payload

    # -- availability -----------------------------------------------------

    def _fetch_pages(self, path: str,
                     params: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collect every offer row across pages, or fail loud.

        Stops on a short page or when ``totalCount`` is reached; a server
        that never shortens a page is cut off by ``MAX_PAGES`` as an
        error, because a silently truncated list is exactly how a provider
        reads as "no capacity".
        """
        rows: List[Dict[str, Any]] = []
        total: Optional[int] = None
        for page in range(1, MAX_PAGES + 1):
            query = dict(params or {})
            query['page'] = page
            query['page_size'] = PAGE_SIZE
            payload = self._call('GET', path, params=query) or {}
            if not isinstance(payload, dict):
                raise PrimeintellectError(
                    f'GET {path}: expected an object, got '
                    f'{type(payload).__name__}')
            items = payload.get('items')
            if not isinstance(items, list):
                raise PrimeintellectError(
                    f'GET {path}: expected a list at .items, got '
                    f'{type(items).__name__ if items is not None else "null"}')
            rows.extend(item for item in items if isinstance(item, dict))
            raw_total = payload.get('totalCount')
            if isinstance(raw_total, int):
                total = raw_total
            if len(items) < PAGE_SIZE:
                return rows
            if total is not None and len(rows) >= total:
                return rows
        raise PrimeintellectError(
            f'GET {path}: more than {MAX_PAGES} pages; refusing to silently '
            'truncate')

    def list_offers(self, *, retry_empty: bool = True) -> List[Dict[str, Any]]:
        """Every in-stock offer, UNFILTERED (server-side filters stay off).

        The flaky-replica quirk: this endpoint intermittently serves
        200-empty responses (see module docstring). With ``retry_empty``
        the empty signature is retried ``EMPTY_FETCH_RETRIES`` times with a
        short backoff before being returned; only a CONSISTENT empty is
        allowed to mean "sold out", and the fetcher still cross-checks
        ``get_gpu_summary`` before writing zero-stock. Deliberately NO
        server-side filters: a filtered empty is ambiguous by construction
        (the quanta lesson), and the caller filters locally.
        """
        for attempt in range(EMPTY_FETCH_RETRIES + 1):
            rows = self._fetch_pages('/availability/gpus', None)
            if rows or not retry_empty:
                return rows
            if attempt < EMPTY_FETCH_RETRIES:
                time.sleep(EMPTY_FETCH_BACKOFF_S)
        return []

    def get_gpu_summary(self) -> Dict[str, Any]:
        """Per-gpuType per-count cheapest prices (the zero-stock
        cross-check's independent second opinion).

        Shape (probed 2026-10-06): ``{gpuType: {"<count>": {"cheapest":
        {"onDemand": ...}, "<region>": {...}}}}`` — a family with stock
        carries priced count entries; the offers list and this summary are
        served by different code paths, which is exactly what a
        cross-check needs.
        """
        payload = self._call('GET', '/availability/gpu-summary') or {}
        if not isinstance(payload, dict):
            raise PrimeintellectError(
                'GET /availability/gpu-summary: expected an object, got '
                f'{type(payload).__name__}')
        return payload

    # -- ssh keys ---------------------------------------------------------

    def list_ssh_keys(self) -> List[Dict[str, Any]]:
        payload = self._call('GET', '/ssh_keys/') or {}
        if not isinstance(payload, dict):
            raise PrimeintellectError('GET /ssh_keys/: expected an object, '
                                      f'got {type(payload).__name__}')
        rows = payload.get('data')
        if not isinstance(rows, list):
            raise PrimeintellectError('GET /ssh_keys/: expected a list at '
                                      f'.data, got {type(rows).__name__}')
        return [row for row in rows if isinstance(row, dict)]

    def create_ssh_key(self, name: str, public_key: str) -> Dict[str, Any]:
        payload = self._call('POST', '/ssh_keys/', body={
            'name': name,
            'publicKey': public_key,
        }) or {}
        if not isinstance(payload, dict) or not payload.get('id'):
            raise PrimeintellectError(
                f'POST /ssh_keys/ returned no id: {sorted(payload)}')
        return payload

    def ensure_ssh_key(self, name: str, public_key: str) -> str:
        """The id of the key matching ``public_key``, adding it if absent.

        Matching on key MATERIAL: this API's key list RETURNS the public
        key material, so the match is exact string equality (no
        fingerprint derivation needed, unlike QuantaCloud). A rename never
        mints a duplicate; a re-created keypair with the same name never
        selects a stale key.
        """
        wanted = public_key.strip()
        for key in self.list_ssh_keys():
            if str(key.get('publicKey') or '').strip() == wanted:
                key_id = key.get('id')
                if key_id:
                    return str(key_id)
        return str(self.create_ssh_key(name, public_key)['id'])

    # -- pods -------------------------------------------------------------

    def create_pod(
        self,
        *,
        name: str,
        offer: Dict[str, Any],
        image: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a pod from a LIVE availability row, echoing it verbatim.

        ``cloudId``/``gpuType``/``socket``/``gpuCount``/``dataCenterId``/
        ``country``/``security`` all come from the SAME offer row — the
        provider validates them as a set, and a mix of rows is a
        validation error at best, a wrong-region box at worst. ``image``
        defaults to the lane's preferred full-OS image when the offer
        carries it, else the offer's first image. ``maxPrice`` is
        deliberately NOT sent (the lane rents fixed-price offers only;
        the docs reserve ``maxPrice`` for variable-price offers).
        """
        required = ('cloudId', 'gpuType', 'socket', 'gpuCount')
        missing = [field for field in required if not offer.get(field)]
        if missing:
            raise PrimeintellectValidationError(
                f'offer row is missing {missing}; refusing to build a pod '
                'body from a partial row')
        images = [str(i) for i in (offer.get('images') or []) if i]
        chosen = image or (PREFERRED_IMAGE if PREFERRED_IMAGE in images
                          else (images[0] if images else PREFERRED_IMAGE))
        pod_body: Dict[str, Any] = {
            'name': name,
            'cloudId': str(offer['cloudId']),
            'gpuType': str(offer['gpuType']),
            'socket': str(offer['socket']),
            'gpuCount': int(offer['gpuCount']),
            'image': chosen,
            'security': str(offer.get('security') or 'secure_cloud'),
        }
        # dataCenterId + country are REQUIRED when the offer carries them
        # (the same cloudId can exist in several datacenters).
        if offer.get('dataCenter'):
            pod_body['dataCenterId'] = str(offer['dataCenter'])
        if offer.get('country'):
            pod_body['country'] = str(offer['country'])
        body: Dict[str, Any] = {
            'pod': pod_body,
            'provider': {'type': str(offer.get('provider') or '')},
        }
        if team_id:
            body['team'] = {'teamId': team_id}
        payload = self._call('POST', '/pods/', body=body) or {}
        if not isinstance(payload, dict) or not payload.get('id'):
            raise PrimeintellectError(
                f'POST /pods/ returned no id: {sorted(payload)}')
        return payload

    def get_pod(self, pod_id: str) -> Dict[str, Any]:
        payload = self._call('GET', f'/pods/{pod_id}') or {}
        if not isinstance(payload, dict):
            raise PrimeintellectError(
                f'GET /pods/{pod_id}: expected an object, got '
                f'{type(payload).__name__}')
        return payload

    def list_pods(self, *, live_only: bool = False) -> List[Dict[str, Any]]:
        """Every pod on the account (paginated), newest first.

        ``live_only`` drops DELETING/TERMINATED rows — the pod list keeps
        terminated pods out of ``/pods/`` already (they move to history),
        but the filter keeps the caller's zero-verification honest.
        """
        rows: List[Dict[str, Any]] = []
        offset = 0
        while True:
            payload = self._call(
                'GET', '/pods/', params={'offset': offset,
                                         'limit': PAGE_SIZE}) or {}
            if not isinstance(payload, dict):
                raise PrimeintellectError('GET /pods/: expected an object, '
                                          f'got {type(payload).__name__}')
            data = payload.get('data')
            if not isinstance(data, list):
                raise PrimeintellectError('GET /pods/: expected a list at '
                                          f'.data, got {type(data).__name__}')
            batch = [row for row in data if isinstance(row, dict)]
            rows.extend(batch)
            total = payload.get('total_count')
            if (len(batch) < PAGE_SIZE or
                    (isinstance(total, int) and len(rows) >= total)):
                break
            offset += len(batch)
            if offset > MAX_PAGES * PAGE_SIZE:
                raise PrimeintellectError('GET /pods/: more than '
                                          f'{MAX_PAGES * PAGE_SIZE} pods; '
                                          'refusing to silently truncate')
        if live_only:
            rows = [row for row in rows
                    if str(row.get('status') or '') not in
                    (STATUS_DELETING, STATUS_TERMINATED)]
        return rows

    def get_pod_status(self, pod_ids: List[str]) -> List[Dict[str, Any]]:
        """Bulk status/SSH/cost rows for the given pods (GET /pods/status/)."""
        if not pod_ids:
            return []
        params = [('pod_ids', str(pid)) for pid in pod_ids]
        payload = self._call('GET', '/pods/status/', params=params) or {}
        if not isinstance(payload, dict):
            raise PrimeintellectError('GET /pods/status/: expected an '
                                      f'object, got {type(payload).__name__}')
        data = payload.get('data')
        if not isinstance(data, list):
            raise PrimeintellectError('GET /pods/status/: expected a list '
                                      f'at .data, got {type(data).__name__}')
        return [row for row in data if isinstance(row, dict)]

    def delete_pod(self, pod_id: str) -> None:
        """DELETE the pod — the ONLY teardown (404 is success).

        There is no stop endpoint; nothing on the pod's local disk
        survives. Idempotent: a 404 means already gone.
        """
        try:
            self._call('DELETE', f'/pods/{pod_id}')
        except PrimeintellectNotFoundError:
            pass

    # -- accessors --------------------------------------------------------

    @staticmethod
    def pod_status(pod: Dict[str, Any]) -> str:
        return str(pod.get('status') or '').strip().upper()

    @staticmethod
    def ssh_user(pod: Dict[str, Any]) -> Optional[str]:
        """The SSH user the PROVIDER reports for this pod.

        ``sshConnection`` is non-null once the pod is ACTIVE (docs example
        says ``root@... -p 22``); the lane MEASURES it on first contact
        (the Latitude lesson: documented root, real ubuntu).
        """
        user, _ = parse_ssh_connection(pod.get('sshConnection'))
        return user

    @staticmethod
    def ssh_port(pod: Dict[str, Any]) -> int:
        """The pod's SSH port from primePortMapping (fallback: the
        connection string, then 22)."""
        mapping = pod.get('primePortMapping')
        if isinstance(mapping, list):
            for entry in mapping:
                if (isinstance(entry, dict) and
                        str(entry.get('usedBy') or '').upper() == 'SSH'):
                    external = str(entry.get('external') or '')
                    if external.isdigit():
                        return int(external)
                if isinstance(entry, dict):
                    used_by = str(entry.get('usedBy') or '').upper()
                    description = str(entry.get('description') or '').upper()
                    if 'SSH' in used_by or 'SSH' in description:
                        external = str(entry.get('external') or '')
                        if external.isdigit():
                            return int(external)
        _, port = parse_ssh_connection(pod.get('sshConnection'))
        return port

    @staticmethod
    def pod_ip(pod: Dict[str, Any]) -> Optional[str]:
        """The pod's public IP (a string or a list — both shapes occur)."""
        ip = pod.get('ip')
        if isinstance(ip, list):
            return str(ip[0]) if ip else None
        return str(ip) if ip else None

    # -- launch-time offer re-resolution -----------------------------------

    def find_offers(
        self,
        *,
        gpu_type: str,
        gpu_count: int,
        data_center: Optional[str] = None,
        max_price_per_gpu: Optional[float] = None,
        vm_class_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Live in-stock offers matching (gpuType, count[, dataCenter][, cap]).

        This is the launch-time re-resolution: offers are a live market
        (the probe watched the list flip 2->0->2 within minutes), so the
        provisioner re-derives the concrete offer — ``cloudId``, provider,
        country and all — from the LIVE list instead of trusting a
        catalog-time snapshot. ``vm_class_only`` applies the VM-class
        upstream allowlist (the deployment model is per-upstream; a
        container-shaped upstream cannot run the bootstrap ladder).
        Sorted by per-GPU price ascending, whole-box price as tiebreaker.
        """
        matches: List[Tuple[float, float, Dict[str, Any]]] = []
        for offer in self.list_offers():
            if str(offer.get('gpuType') or '') != gpu_type:
                continue
            count = offer.get('gpuCount')
            if not isinstance(count, int) or count != gpu_count:
                continue
            if data_center is not None and str(
                    offer.get('dataCenter') or '') != data_center:
                continue
            if vm_class_only and not is_vm_class_upstream(
                    str(offer.get('provider') or '')):
                continue
            stock = str(offer.get('stockStatus') or '').lower()
            if stock and stock not in ('available', 'low'):
                continue  # an unknown/unavailable stock flag is not rentable
            prices = offer.get('prices') or {}
            raw_price = prices.get('onDemand') if isinstance(
                prices, dict) else None
            if not isinstance(raw_price, (int, float)):
                continue  # unpriceable: skip rather than quote as free
            whole_box = float(raw_price)
            if max_price_per_gpu is not None:
                if whole_box / max(gpu_count, 1) > max_price_per_gpu:
                    continue
            matches.append((whole_box / max(gpu_count, 1), whole_box, offer))
        matches.sort(key=lambda entry: (entry[0], entry[1]))
        return [offer for _, _, offer in matches]

    # -- polling ----------------------------------------------------------

    def wait_until_active(
        self,
        pod_id: str,
        *,
        timeout_s: float = DEFAULT_PROVISION_TIMEOUT_S,
        poll_interval_s: float = 12.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> Dict[str, Any]:
        """Poll until the pod is ACTIVE, or fail closed.

        ``ERROR`` raises immediately with the provider's own
        ``installationFailure`` (never auto-retry — the operator decides),
        and so do ``TERMINATED``/``DELETING`` (the box vanished while we
        waited) and the deadline: a stuck PROVISIONING must become a
        failed runner, not an endless wait (the failure mode that kept a
        Vast instance SkyPilotProvisioning for 30 minutes while the
        provider had already given up).
        """
        if poll_interval_s < MIN_POLL_INTERVAL_S:
            raise PrimeintellectError(
                f'poll_interval_s={poll_interval_s} is below the floor of '
                f'{MIN_POLL_INTERVAL_S}s')
        deadline = now() + timeout_s
        last_status: Optional[str] = None
        while True:
            pod = self.get_pod(pod_id)
            status = self.pod_status(pod)
            if status:
                last_status = status
            if status == STATUS_ACTIVE:
                return pod
            if status == STATUS_ERROR:
                failure = str(pod.get('installationFailure') or '')
                suffix = f' ({failure})' if failure else ''
                raise PrimeintellectError(
                    f'pod {pod_id} entered ERROR{suffix} while waiting for '
                    'ACTIVE; not auto-retrying (report and decide)')
            if status in (STATUS_TERMINATED, STATUS_DELETING):
                raise PrimeintellectError(
                    f'pod {pod_id} is {status} while waiting for ACTIVE — '
                    'the box was deleted mid-provision (check the wallet: '
                    'Prime auto-deletes pods when credits run out)')
            if now() >= deadline:
                last = repr(last_status or STATUS_PROVISIONING)
                raise PrimeintellectError(
                    f'pod {pod_id} still {last} after {timeout_s:.0f}s; '
                    'refusing to wait longer')
            sleep(poll_interval_s)
