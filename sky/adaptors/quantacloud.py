"""QuantaCloud REST client.

QuantaCloud (https://quantacloud.net) rents on-demand NVIDIA GPU VMs from a
prepaid credit balance: one offer (a GPU model x count x region x price) is
one deployable box, API deployments always boot the single stock image
(Ubuntu 22.04 + NVIDIA drivers + Docker), and stopping a deployment
TERMINATES it and deletes its disk (there is no stop-and-keep).

Wire protocol notes that differ from the sibling brokers (each verified
against https://docs.quantacloud.net/api-reference/overview on 2026-10-06):

* **Plain JSON**, not JSON:API. Errors are one consistent envelope:
  ``{"error": {"code": ..., "message": ..., ...}}``. The envelope is
  preserved into the raised error verbatim — flattening it to a status line
  is the bug class that made a Spheron stockout read as an auth failure.
* **402 is a CONFIG error, not capacity.** ``insufficient_balance`` carries
  ``required_balance``/``current_balance``: the account could not cover one
  whole-box hour. A lane that mistakes it for "no stock" hunts forever on a
  billing problem (the Spheron funding lesson).
* **The offers endpoint returns ONLY in-stock offers**, and a filtered
  query that matches nothing (bogus slug, empty continent) returns the same
  ``200 {offers: [], totalElements: 0}`` as honest no-stock. Nothing in
  this client applies server-side filters: fetch everything (unfiltered)
  and filter locally, so an empty result can only mean "sold out".
* **Offer UUIDs are ephemeral.** An offer that goes out of stock 404s on
  ``GET /offers/{id}`` and refuses at deploy (``offer_unavailable``). The
  launch path re-resolves a concrete offer from the LIVE list (see
  ``find_offers``) rather than trusting a UUID fetched at catalog time.
* **Deployments carry no name/label/tag.** The create body takes
  provider/offer/ssh keys only, so server-side adoption-by-name (the
  Latitude pattern) does not exist; the provisioner keeps a local
  cluster-name -> deployment-id mapping instead.

Every call carries an explicit timeout (a hung HTTP call once wedged a
runner for 30+ minutes), and non-JSON or non-dict payloads raise typed
errors rather than leaking AttributeError/UnicodeDecodeError.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

API_BASE = 'https://core.quantacloud.net/api/v1'

# Identifies the integration to the provider's logs.
USER_AGENT = 'urun-skypilot-quantacloud/0.1 (+https://urun.sh)'

# One timeout for every request, including the reads inside poll loops —
# the point is the bound, not the value.
DEFAULT_TIMEOUT_S = 60

# Documented budget (https://docs.quantacloud.net/api-reference/overview):
# 3,000 requests per 15 minutes per IP (the budget is per-IP, not per-key,
# so a controller pod shares ONE budget across all its calls). 429 bodies
# carry no retry hint; back off exponentially, bounded, three strikes.
RATE_LIMIT_REQUESTS_PER_15MIN = 3000
MAX_RATE_LIMIT_RETRIES = 3
MAX_RETRY_AFTER_S = 60.0

# Pagination: page/size, 0-indexed, max size 100. Loop until a short page;
# the cap turns a server that ignores pagination into a loud error instead
# of a silently truncated catalog.
PAGE_SIZE = 100
MAX_PAGES = 100

# Deployment status steady states (docs: deployments.md). provisioning and
# connecting are transitional; stopping heads to terminated; failed and
# interrupted are terminal failures.
STATUS_PROVISIONING = 'provisioning'
STATUS_CONNECTING = 'connecting'
STATUS_ACTIVE = 'active'
STATUS_STOPPING = 'stopping'
STATUS_TERMINATED = 'terminated'
STATUS_FAILED = 'failed'
STATUS_INTERRUPTED = 'interrupted'

# The provider's documented deploy poll cadence ("about 3 minutes for most
# instances; around 10 for 8-GPU"); polling faster is rate-budget waste.
MIN_POLL_INTERVAL_S = 10

# ~10-minute worst-case documented provisioning plus margin.
DEFAULT_PROVISION_TIMEOUT_S = 20 * 60

# The ONLY provider value (docs: providers.md) — every create body carries
# it verbatim; anything else is `provider_not_found`.
PROVIDER_SLUG = 'quantacloud'

# Environment variable the callers read the key from (the task secret
# channel's spelling; there is no vendor alias to normalize, unlike Vast's
# VAST_AI_API_KEY).
API_KEY_ENV = 'QUANTACLOUD_API_KEY'
API_KEY_FILE = '~/.quanta/credentials'

# GPU slugs that are MIG partitions of a larger GPU (e.g.
# rtx-pro-6000-blackwell-mig-48gb). A MIG slice must never price as the
# whole GPU: the accelerator token would claim vramGB the slice does not
# have, and a claim would rent a partition believing it rented the card.
MIG_SLUG_MARKER = '-mig-'


class QuantacloudError(RuntimeError):
    """Any QuantaCloud API failure. Never raised with the API key in it.

    Carries the provider's own error code when the response had one, so
    callers can branch on ``insufficient_balance`` vs ``offer_unavailable``
    vs ``rate_limited`` instead of parsing strings (the shadeform
    INSUFFICIENT_FUNDS lesson).
    """

    def __init__(self, message: str, *, code: str = ''):
        super().__init__(message)
        self.code = code


class QuantacloudAuthError(QuantacloudError):
    """401 — key missing, invalid, or revoked. Check/rotate it."""


class QuantacloudInsufficientBalanceError(QuantacloudError):
    """402 — the account cannot cover one hour at the offer's rate.

    A CONFIG error, not a capacity fact: the deploy was refused before
    anything was created or charged. Carries the provider's own numbers.
    """

    def __init__(self,
                 message: str,
                 *,
                 code: str = 'insufficient_balance',
                 required_balance: str = '',
                 current_balance: str = ''):
        super().__init__(message, code=code)
        self.required_balance = required_balance
        self.current_balance = current_balance


class QuantacloudRateLimited(QuantacloudError):
    """429 that exhausted its retries. No documented body hint; bounded
    exponential backoff already ran."""


class QuantacloudOfferUnavailableError(QuantacloudError):
    """offer_unavailable — the offer went out of stock or was withdrawn
    between fetch and deploy. Capacity-shaped: retry resolves it via a
    fresh offer, not a wait."""

    def __init__(self, message: str):
        super().__init__(message, code='offer_unavailable')


class QuantacloudCoolingDownError(QuantacloudError):
    """503 offer_cooling_down — a recent attempt hit capacity for this
    exact offer+location. Carries the provider's own retry_after seconds."""

    def __init__(self, message: str, *, retry_after_s: Optional[float]):
        super().__init__(message, code='offer_cooling_down')
        self.retry_after_s = retry_after_s


class QuantacloudNotFoundError(QuantacloudError):
    """404 — the resource is gone. Terminate paths treat this as success."""


def _error_envelope(raw: bytes) -> Dict[str, Any]:
    """The provider's error object from a body, or {} if unusable."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            err = parsed.get('error')
            if isinstance(err, dict):
                return err
    except (ValueError, AttributeError):
        pass
    return {}


def _retry_after_s(raw: bytes) -> Optional[float]:
    """The provider's own retry hint from a 503 body, if present and sane.

    ``offer_cooling_down`` puts it at ``error.retry_after`` (seconds); a
    negative or absurd value is discarded rather than trusted (the sleep is
    bounded regardless).
    """
    err = _error_envelope(raw)
    value = err.get('retry_after')
    if value is None:
        return None
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        return None
    if 0 < value_f <= 3600:
        return value_f
    return None


def _ssh_fingerprint(public_key: str) -> str:
    """The ``SHA256:...`` fingerprint of an OpenSSH public key.

    The key list does not return key material, but it returns this
    fingerprint — the same value ``ssh-keygen -lf`` prints, i.e.
    base64(sha256(blob)) over the key's wire-format blob (the second
    field of the public-key line). Deriving it locally makes
    ``ensure_ssh_key`` an exact MATERIAL match (a rename never mints a
    duplicate; a re-created keypair with the same name never selects a
    stale key).
    """

    parts = public_key.strip().split()
    if len(parts) < 2:
        raise QuantacloudError(
            'public key does not look like an OpenSSH public key line '
            '(expected: algorithm, base64 blob, optional comment)')
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except Exception as exc:  # binascii.Error subclasses ValueError
        raise QuantacloudError(
            f'public key blob is not valid base64: {exc}') from exc
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode('ascii')
    return f'SHA256:{digest}'


class QuantacloudClient:
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
            raise QuantacloudAuthError(
                'QuantaCloud API key is empty; refusing to make '
                'unauthenticated calls that would look like "no capacity"')
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
                # stalled body raises TimeoutError/OSError directly and would
                # escape every caller that only catches QuantacloudError (the
                # trap CodeRabbit flagged on the Spheron client).
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read()
            except (TimeoutError, OSError) as read_exc:
                raise QuantacloudError(
                    f'{method} {path} failed reading the error '
                    f'body: {read_exc}') from read_exc
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise QuantacloudError(
                f'{method} {path} failed: {exc.reason}') from exc
        except (TimeoutError, OSError) as exc:
            raise QuantacloudError(f'{method} {path} failed: {exc}') from exc

    def _request_once(self, method: str, url: str, headers: Dict[str, str],
                      payload: Optional[bytes]):
        if self._transport is not None:
            status, raw = self._transport(method, url, headers, payload)
        else:
            status, raw = self._http(method, url, headers, payload)
        return status, raw

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        public: bool = False,
    ) -> Any:
        url = f'{self._base_url}{path}'
        if params:
            url = f'{url}?{urllib.parse.urlencode(params)}'
        headers = {
            'User-Agent': USER_AGENT,
            'Accept': 'application/json',
        }
        if not public:
            # The docs bless both X-API-Key and Bearer; X-API-Key is the
            # header the console's own examples lead with.
            headers['X-API-Key'] = self._api_key
        payload = None
        if body is not None:
            payload = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            status, raw = self._request_once(method, url, headers, payload)
            if status == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                # No documented retry hint on 429 bodies: bounded exponential.
                time.sleep(min(2.0 * (attempt + 1), MAX_RETRY_AFTER_S))
                continue
            return self._interpret(status, raw, method, path)

        # Unreachable: the loop returns or raises on the final attempt.
        raise AssertionError('rate-limit retry loop exhausted')

    def _interpret(self, status: int, raw: bytes, method: str, path: str):
        # Never include the URL query or headers in an error: no key leakage.
        where = f'{method} {path}'
        if status < 400:
            # The empty-body success path lives UNDER the status check: a
            # 4xx/5xx with an empty body is a FAILURE (raise below), never
            # a silent None — the bug class where a provider's empty-body
            # error would read as "done" while the box keeps billing.
            if not raw:
                return None
            try:
                return json.loads(raw)
            # ValueError covers JSONDecodeError AND UnicodeDecodeError (a
            # non-UTF-8 gateway page is a ValueError, not a JSONDecodeError)
            # so no raw exception escapes a QuantacloudError-only caller.
            except ValueError as exc:
                raise QuantacloudError(
                    f'{where}: response was not JSON ({exc})') from exc

        err = _error_envelope(raw)
        code = str(err.get('code') or '')
        message = str(err.get('message') or '')[:300]
        if not code and not message:
            # An error with no usable envelope still must not collapse to a
            # bare status line: show the raw prefix instead.
            message = raw[:200].decode('utf-8', 'replace')

        if status == 401:
            raise QuantacloudAuthError(
                f'{where}: HTTP 401. Check {API_KEY_ENV} (or rotate the key '
                'in the QuantaCloud console under API Keys).')
        if status == 402:
            code = code or 'insufficient_balance'
            raise QuantacloudInsufficientBalanceError(
                f'{where}: {code}: {message}',
                code=code,
                required_balance=str(err.get('required_balance') or ''),
                current_balance=str(err.get('current_balance') or ''),
            )
        if status == 404:
            raise QuantacloudNotFoundError(f'{where}: not found ({message})')
        if status == 429:
            default = code or 'rate_limited'
            raise QuantacloudRateLimited(
                f'{where}: rate limited ({default}); detail: {message}')
        if status == 503 and code == 'offer_cooling_down':
            raise QuantacloudCoolingDownError(
                f'{where}: offer cooling down: {message}',
                retry_after_s=_retry_after_s(raw),
            )
        if code == 'offer_unavailable':
            raise QuantacloudOfferUnavailableError(f'{where}: {message}')
        default = code or 'error'
        raise QuantacloudError(
            f'{where}: HTTP {status}: {default}: {message}',
            code=code,
        )

    def _paginate(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        public: bool = False,
        rows_key: str = 'offers',
    ) -> List[Dict[str, Any]]:
        """Collect every element across pages, or fail loud.

        Stops on a short page; a server that never shortens a page is cut
        off by ``MAX_PAGES`` as an error, because a silently truncated list
        is exactly how a provider reads as "no capacity".
        """
        out: List[Dict[str, Any]] = []
        for page in range(MAX_PAGES):
            query = dict(params or {})
            query['page'] = page
            query['size'] = PAGE_SIZE
            payload = self._call('GET', path, params=query, public=public) or {}
            if not isinstance(payload, dict):
                raise QuantacloudError(f'GET {path}: expected an object, got '
                                       f'{type(payload).__name__}')
            rows = payload.get(rows_key)
            if not isinstance(rows, list):
                raise QuantacloudError(
                    f'GET {path}: expected a list at .{rows_key}, got '
                    f'{type(rows).__name__}')
            out.extend(row for row in rows if isinstance(row, dict))
            if len(rows) < PAGE_SIZE:
                return out
        raise QuantacloudError(
            f'GET {path}: more than {MAX_PAGES} pages; refusing to silently '
            'truncate')

    # -- account ----------------------------------------------------------

    def get_account(self) -> Dict[str, Any]:
        payload = self._call('GET', '/account') or {}
        if not isinstance(payload, dict):
            raise QuantacloudError('GET /account: expected an object, got '
                                   f'{type(payload).__name__}')
        return payload

    def get_balance(self) -> Dict[str, Any]:
        payload = self._call('GET', '/account/balance') or {}
        if not isinstance(payload, dict):
            raise QuantacloudError(
                'GET /account/balance: expected an object, got '
                f'{type(payload).__name__}')
        return payload

    # -- catalog (public endpoints) ----------------------------------------

    def list_gpu_families(self) -> List[Dict[str, Any]]:
        """GPU families with availability counts (public, no auth).

        The lane's zero-stock cross-check: ``availableCount`` is the
        provider's own per-family in-stock count, so an offers list that
        came back empty while a family reports stock is a contradiction
        (filter bug or API drift), never an honest "sold out".
        """
        payload = self._call('GET', '/gpu-families', public=True) or {}
        if not isinstance(payload, dict):
            raise QuantacloudError('GET /gpu-families: expected an object, got '
                                   f'{type(payload).__name__}')
        rows = payload.get('data')
        if not isinstance(rows, list):
            raise QuantacloudError(
                'GET /gpu-families: expected a list at .data, got '
                f'{type(rows).__name__}')
        return [row for row in rows if isinstance(row, dict)]

    def list_offers(self) -> List[Dict[str, Any]]:
        """Every IN-STOCK offer, unfiltered.

        Deliberately NO server-side filters: the offers endpoint treats a
        matching-nothing filter the same as honest no-stock (``200`` with
        ``totalElements: 0``), so an empty result here can only mean "sold
        out". Callers filter locally (``find_offers`` / the catalog
        fetcher).
        """
        return self._paginate('/offers', public=True)

    def get_offer(self, offer_id: str) -> Dict[str, Any]:
        payload = self._call('GET', f'/offers/{offer_id}', public=True) or {}
        if not isinstance(payload, dict):
            raise QuantacloudError(
                f'GET /offers/{offer_id}: expected an object, got '
                f'{type(payload).__name__}')
        return payload

    # -- ssh keys ---------------------------------------------------------

    def list_ssh_keys(self) -> List[Dict[str, Any]]:
        payload = self._call('GET', '/users/me/ssh-keys') or {}
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        raise QuantacloudError('GET /users/me/ssh-keys: expected a list, got '
                               f'{type(payload).__name__}')

    def create_ssh_key(self, name: str, public_key: str) -> Dict[str, Any]:
        payload = self._call(
            'POST',
            '/users/me/ssh-keys',
            body={
                'name': name,
                'publicKey': public_key
            },
        ) or {}
        if not isinstance(payload, dict) or not payload.get('id'):
            raise QuantacloudError(
                f'POST /users/me/ssh-keys returned no id: {sorted(payload)}')
        return payload

    def ensure_ssh_key(self, name: str, public_key: str) -> str:
        """The id of the key matching ``public_key``, adding it if absent.

        Matching on key MATERIAL (not name): a re-created controller
        keypair with the same name must not silently select a stale key,
        and a re-deploy with a renamed key must not mint duplicates. The
        key list does not return material, but it returns the SHA256
        fingerprint — which is derivable locally from the public key blob
        (the same value ``ssh-keygen -lf`` prints), so material identity is
        still exact.
        """
        fingerprint = _ssh_fingerprint(public_key)
        for key in self.list_ssh_keys():
            if str(key.get('fingerprint') or '') == fingerprint:
                key_id = key.get('id')
                if key_id:
                    return str(key_id)
        return str(self.create_ssh_key(name, public_key)['id'])

    # -- deployments ------------------------------------------------------

    def create_deployment(
        self,
        *,
        offer_id: str,
        ssh_key_ids: Optional[List[str]] = None,
        provider: str = PROVIDER_SLUG,
    ) -> Dict[str, Any]:
        """Create a deployment; returns ``{id, status}`` (202).

        ``ssh_key_ids`` omitted/empty injects EVERY key on the account
        (documented); the provisioner always passes the lane's ensured key
        id explicitly.
        """
        body: Dict[str, Any] = {'provider': provider, 'offer_id': offer_id}
        if ssh_key_ids:
            body['ssh_key_ids'] = ssh_key_ids
        payload = self._call('POST', '/deployments', body=body) or {}
        if not isinstance(payload, dict) or not payload.get('id'):
            raise QuantacloudError(
                f'POST /deployments returned no id: {sorted(payload)}')
        return payload

    def get_deployment(self, deployment_id: str) -> Dict[str, Any]:
        payload = self._call('GET', f'/deployments/{deployment_id}') or {}
        if not isinstance(payload, dict):
            raise QuantacloudError(
                f'GET /deployments/{deployment_id}: expected an object, got '
                f'{type(payload).__name__}')
        return payload

    def stop_deployment(self, deployment_id: str) -> None:
        """POST stop == DELETE: the only teardown, and the disk is deleted.

        404 is success (idempotent teardown); a ``validation_error`` saying
        the deployment cannot be stopped (already stopping/terminated/
        failed) is also success — the box is already going or gone.
        """
        try:
            self._call('POST', f'/deployments/{deployment_id}/stop')
        except QuantacloudNotFoundError:
            pass
        except QuantacloudError as exc:
            if (exc.code == 'validation_error' and
                    'cannot stop' in str(exc).lower()):
                return
            raise

    def list_deployments(self) -> List[Dict[str, Any]]:
        """Every deployment on the account, newest first (paged)."""
        return self._paginate('/deployments', rows_key='data')

    # -- accessors --------------------------------------------------------

    @staticmethod
    def deployment_status(deployment: Dict[str, Any]) -> str:
        return str(deployment.get('status') or '').strip().lower()

    @staticmethod
    def connection(deployment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        conn = deployment.get('connection')
        return conn if isinstance(conn, dict) else None

    @staticmethod
    def ssh_user(deployment: Dict[str, Any]) -> Optional[str]:
        """The SSH user the PROVIDER reports for this deployment.

        Non-null only once the deployment is ``active``. The docs SAY
        ``ubuntu``; the lane measures it on first contact anyway (the
        Latitude lesson: documented root, real ubuntu).
        """
        conn = QuantacloudClient.connection(deployment)
        user = (conn or {}).get('ssh_user')
        return str(user) if user else None

    # -- launch-time offer re-resolution -----------------------------------

    def find_offers(
        self,
        *,
        gpu_slug: str,
        gpu_count: int,
        region: Optional[str] = None,
        max_price_per_gpu: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Live in-stock offers matching (slug, count[, region][, cap]).

        This is the launch-time re-resolution: offer UUIDs are ephemeral,
        so the provisioner re-derives the concrete offer from the live list
        instead of trusting a catalog-time UUID. Sorted by pricePerGpu
        ascending (the provider's own default ordering); MIG slugs are
        excluded here too — a partition must never satisfy a whole-GPU
        request.
        """
        matches = []
        for offer in self.list_offers():
            gpu = offer.get('gpu') or {}
            slug = str(gpu.get('slug') or '')
            count = gpu.get('count') or 0
            if slug != gpu_slug or MIG_SLUG_MARKER in slug:
                continue
            if not isinstance(count, int) or count != gpu_count:
                continue
            if region is not None and str(offer.get('region') or '') != region:
                continue
            if not offer.get('isAvailable', False):
                continue
            if max_price_per_gpu is not None:
                raw_price = offer.get('pricePerGpu')
                if not isinstance(raw_price, (int, float, str)):
                    continue
                try:
                    per_gpu = float(raw_price)
                except ValueError:
                    continue
                if per_gpu > max_price_per_gpu:
                    continue
            matches.append(offer)
        matches.sort(key=lambda o: float(o.get('pricePerGpu') or float('inf')))
        return matches

    # -- polling ----------------------------------------------------------

    def wait_until_active(
        self,
        deployment_id: str,
        *,
        timeout_s: float = DEFAULT_PROVISION_TIMEOUT_S,
        poll_interval_s: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> Dict[str, Any]:
        """Poll until the deployment is ``active``, or fail closed.

        ``failed`` and ``interrupted`` raise immediately (never
        auto-retry — the operator decides), and so does the deadline: a
        stuck provisioning must become a failed runner, not an endless
        wait (the failure mode that kept a Vast instance
        ``SkyPilotProvisioning`` for 30 minutes while the provider had
        already given up).
        """
        if poll_interval_s < MIN_POLL_INTERVAL_S:
            raise QuantacloudError(
                f'poll_interval_s={poll_interval_s} is below the documented '
                f'floor of {MIN_POLL_INTERVAL_S}s')
        deadline = now() + timeout_s
        last_status: Optional[str] = None
        while True:
            deployment = self.get_deployment(deployment_id)
            status = self.deployment_status(deployment)
            if status:
                last_status = status
            if status == STATUS_ACTIVE:
                return deployment
            if status in (STATUS_FAILED, STATUS_INTERRUPTED):
                failure = str(deployment.get('failure_code') or '')
                suffix = f' ({failure})' if failure else ''
                raise QuantacloudError(
                    f'deployment {deployment_id} entered {status}{suffix} '
                    'while waiting for active; not auto-retrying (report '
                    'and decide)')
            if now() >= deadline:
                last = repr(last_status or 'provisioning')
                raise QuantacloudError(
                    f'deployment {deployment_id} still {last} after '
                    f'{timeout_s:.0f}s; refusing to wait longer')
            sleep(poll_interval_s)
