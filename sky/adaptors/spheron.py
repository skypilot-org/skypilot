"""Spheron REST client.

The one place that talks to ``app.spheron.ai``. Everything above it (the
SkyPilot cloud class, the provisioner, the catalog fetcher) goes through here so
auth, the user-agent quirk, rate limiting and error shape are handled once.

Behaviours that are NOT optional, each learned the hard way on 2026-09-10:

* **An explicit User-Agent is mandatory.** The API returns ``403 Forbidden`` to
  urllib's default UA while accepting the identical request from curl. Without
  this every call fails with a status that looks like an auth problem.
* **Two separate rate limits**: 250 requests / 15 min / IP for most endpoints,
  but only **10 deployment CREATES / 15 min / user**. The manual is explicit --
  "Never loop deployment creation." ``429`` is surfaced as its own retryable
  error carrying the documented ``retryAfter``.
* **A deployment must be built from ONE offer.** ``provider``, ``offerId``,
  ``gpuType``, ``gpuCount``, ``region`` and ``operatingSystem`` all have to come
  from the same ``offers[]`` entry or the API rejects it (or worse, behaves
  unexpectedly). ``DeploymentRequest.from_offer`` is the only supported way to
  build one, so the fields cannot drift apart.

No silent fallbacks: every failure raises. A call that cannot be made must be
loud, because the alternative — an empty offer list or a swallowed deploy error
— reads as "no capacity" and sends people looking in the wrong place.
"""

from __future__ import annotations

import dataclasses
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

API_BASE = "https://app.spheron.ai"

# See module docstring: the API 403s urllib's default agent.
USER_AGENT = "urun-skypilot-spheron/0.1 (+https://urun.sh)"

DEFAULT_TIMEOUT_S = 60

# Documented quotas, so callers can budget rather than discover a 429 mid-provision.
#
# NOTE a source conflict: the API landing page states "100 requests per 15
# minutes, per IP", while the Spheron skill manual (section 9) splits it into
# the two limits below. We follow the manual because it is the more specific
# and more recent statement, and because the deploy limit it documents has no
# equivalent on the landing page at all. If 429s appear well under 250, assume
# the landing page is right and lower RATE_LIMIT_REQUESTS.
RATE_LIMIT_REQUESTS = 250  # all endpoints except deployment creation
RATE_LIMIT_WINDOW_S = 15 * 60
# POST /api/deployments is far tighter, and per USER rather than per IP.
# "Never loop deployment creation."
RATE_LIMIT_DEPLOY_CREATES = 10

# Terminal and transitional deployment states.
STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"
STATUS_TERMINATED = "terminated"
# Reclaimed by the provider. On SPOT this means the capacity was interrupted --
# it is NOT a user action and NOT a retryable failure; the instance is gone.
STATUS_TERMINATED_PROVIDER = "terminated-provider"

_PENDING_STATUSES = frozenset({"deploying"})
_FAILED_STATUSES = frozenset({"failed"})
# Everything from which a deployment will never reach `running` again.
_TERMINAL_STATUSES = (
    frozenset({STATUS_TERMINATED, STATUS_TERMINATED_PROVIDER}) | _FAILED_STATUSES
)
# `?status=active` covers these, per the documented filter semantics.
ACTIVE_STATUSES = frozenset({STATUS_RUNNING, "deploying", STATUS_STOPPED})

# Minimum documented poll spacing. Going faster is explicitly called out as
# abuse in the manual ("Do not poll faster than once every 10 seconds").
MIN_POLL_INTERVAL_S = 10


class SpheronError(RuntimeError):
    """Any Spheron API failure. Never raised with the API key in the message."""


class SpheronAuthError(SpheronError):
    """401/403 — key missing, malformed, invalid, or the UA was rejected."""


class SpheronRateLimited(SpheronError):
    """429 — retryable. Back off; do not retry immediately."""

    def __init__(self, message: str, retry_after_s: Optional[float] = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class SpheronInsufficientBalance(SpheronError):
    """The team has no credit. A deploy cannot succeed until it is funded."""


@dataclasses.dataclass(frozen=True)
class Offer:
    """One concrete, deployable configuration, as returned by /api/gpu-offers.

    Deliberately carries the whole set of deploy-critical fields together so a
    caller cannot mix one offer's ``offerId`` with another's ``gpuType``.
    """

    provider: str
    offer_id: str
    gpu_type: str
    gpu_count: int
    gpu_memory_gb: int
    vcpus: float
    memory_gb: float
    price_per_hour_usd: float
    regions: List[str]
    os_options: List[str]
    instance_type: str
    supports_cloud_init: bool
    maintenance: bool
    minimum_runtime_minutes: Optional[int]

    @classmethod
    def from_payload(cls, group_gpu_type: str, raw: Dict[str, Any]) -> "Offer":
        missing = [
            k
            for k in ("provider", "offerId", "gpuCount", "price")
            if raw.get(k) is None
        ]
        if missing:
            raise SpheronError(
                f"offer is missing required fields {missing}: {sorted(raw)}"
            )
        return cls(
            provider=str(raw["provider"]),
            offer_id=str(raw["offerId"]),
            gpu_type=group_gpu_type,
            gpu_count=int(raw["gpuCount"]),
            gpu_memory_gb=int(raw.get("gpu_memory") or 0),
            vcpus=float(raw.get("vcpus") or 0),
            memory_gb=float(raw.get("memory") or 0),
            # Dollars, NOT cents. See catalog/fetch_spheron.py.
            price_per_hour_usd=float(raw["price"]),
            regions=list(raw.get("clusters") or []),
            os_options=list(raw.get("os_options") or []),
            instance_type=str(raw.get("instanceType") or "DEDICATED"),
            supports_cloud_init=bool(raw.get("supportsCloudInit")),
            maintenance=bool(raw.get("maintenance")),
            minimum_runtime_minutes=raw.get("minimumRuntimeMinutes"),
        )

    def pick_os(
        self,
        prefer_substrings: Optional[List[str]] = None,
        *,
        required: bool = True,
    ) -> str:
        """Choose an OS image from this offer's own options.

        Fails hard when nothing matches rather than guessing: an OS string the
        offer does not list is rejected by the deploy call, and a CUDA-less
        image would boot fine and then fail at import time ~90 minutes in.
        """
        if not self.os_options:
            raise SpheronError(f"offer {self.offer_id!r} lists no os_options")
        for needle in prefer_substrings or []:
            for option in self.os_options:
                if needle.lower() in option.lower():
                    return option
        # `required=True` (the DEFAULT) keeps this a strict primitive: asking
        # for an OS the offer does not list is a caller error and raises. Some
        # Spheron providers DO ship cuda images -- e.g. sesterce offers
        # "Ubuntu Server 22.04 LTS R570 CUDA 12.8" -- so a silent fallback
        # would hide a genuinely wrong preference.
        #
        # `required=False` is for the one caller that must tolerate absence:
        # massed-compute's GPU offers list exactly one OS ("Ubuntu Server
        # 22.04") and no cuda image at all, so requiring one made EVERY such
        # offer unprovisionable:
        #   SpheronError: offer 'gpu_1x_pro_6000_blackwell_us-central-9' has no
        #   OS matching ['cuda']; available: ['Ubuntu Server 22.04']
        # That is safe ONLY because the CUDA-less risk is caught one layer down
        # and for free: urun's bootstrap runs `runtime-gpu-probe`, a $0
        # `docker run --rm --gpus all <base> nvidia-smi -L` that refuses loudly
        # BEFORE the paid runtime boot.
        if prefer_substrings and required:
            raise SpheronError(
                f"offer {self.offer_id!r} has no OS matching "
                f"{prefer_substrings}; available: {self.os_options}"
            )
        return self.os_options[0]


@dataclasses.dataclass(frozen=True)
class DeploymentRequest:
    """A validated POST /api/deployments body.

    Build via :meth:`from_offer` so the coupled fields cannot drift.
    """

    provider: str
    offer_id: str
    gpu_type: str
    gpu_count: int
    region: str
    operating_system: str
    instance_type: str
    ssh_key_id: Optional[str] = None
    ssh_public_key: Optional[str] = None
    name: Optional[str] = None
    team_id: Optional[str] = None
    cloud_init: Optional[Dict[str, Any]] = None

    @classmethod
    def from_offer(
        cls,
        offer: Offer,
        region: str,
        operating_system: str,
        *,
        ssh_key_id: Optional[str] = None,
        ssh_public_key: Optional[str] = None,
        name: Optional[str] = None,
        team_id: Optional[str] = None,
        cloud_init: Optional[Dict[str, Any]] = None,
    ) -> "DeploymentRequest":
        if region not in offer.regions:
            raise SpheronError(
                f"region {region!r} is not offered by {offer.offer_id!r} "
                f"(has {offer.regions})"
            )
        if operating_system not in offer.os_options:
            raise SpheronError(
                f"OS {operating_system!r} is not offered by {offer.offer_id!r}"
            )
        if (ssh_key_id is None) == (ssh_public_key is None):
            raise SpheronError(
                "exactly one of ssh_key_id / ssh_public_key is required; "
                "without one the instance is unreachable"
            )
        if cloud_init is not None and not offer.supports_cloud_init:
            # 56% of offers report supportsCloudInit: false. Silently dropping
            # the cloud-init would produce a VM that boots and never bootstraps.
            raise SpheronError(
                f"offer {offer.offer_id!r} does not support cloudInit; "
                "bootstrap must go over SSH for this offer"
            )
        return cls(
            provider=offer.provider,
            offer_id=offer.offer_id,
            gpu_type=offer.gpu_type,
            gpu_count=offer.gpu_count,
            region=region,
            operating_system=operating_system,
            instance_type=offer.instance_type,
            ssh_key_id=ssh_key_id,
            ssh_public_key=ssh_public_key,
            name=name,
            team_id=team_id,
            cloud_init=cloud_init,
        )

    def to_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "provider": self.provider,
            "offerId": self.offer_id,
            "gpuType": self.gpu_type,
            # 0 is a real value (CPU node), so this must not be falsy-filtered.
            "gpuCount": self.gpu_count,
            "region": self.region,
            "operatingSystem": self.operating_system,
            "instanceType": self.instance_type,
        }
        if self.ssh_key_id is not None:
            body["sshKeyId"] = self.ssh_key_id
        if self.ssh_public_key is not None:
            body["ssh_public_key"] = self.ssh_public_key
        if self.name is not None:
            body["name"] = self.name
        if self.team_id is not None:
            body["teamId"] = self.team_id
        if self.cloud_init is not None:
            body["cloudInit"] = self.cloud_init
        return body


class SpheronClient:
    """Thin, explicit client. One method per endpoint we actually use."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = API_BASE,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        transport: Optional[Any] = None,
    ):
        if not api_key or not api_key.strip():
            raise SpheronAuthError(
                "Spheron API key is empty; refusing to make unauthenticated "
                'calls that would look like "no capacity"'
            )
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        # Injected in tests. Signature: (method, url, headers, body) -> (status, bytes)
        self._transport = transport

    # -- plumbing ---------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        if self._transport is not None:
            status, raw = self._transport(method, url, headers, payload)
        else:
            status, raw = self._http(method, url, headers, payload)

        return self._interpret(status, raw, method, path)

    def _http(
        self, method: str, url: str, headers: Dict[str, str], payload: Optional[bytes]
    ):
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                # `resp.read()` runs OUTSIDE urlopen's URLError handling: a
                # stalled response body raises TimeoutError/OSError directly,
                # which would escape as an unclassified exception past every
                # caller that only catches SpheronError. (CodeRabbit on #338.)
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read()
            except (TimeoutError, OSError) as read_exc:
                raise SpheronError(
                    f"{method} {url.split('?')[0]} failed reading the error "
                    f"body: {read_exc}"
                ) from read_exc
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise SpheronError(
                f"{method} {url.split('?')[0]} failed: {exc.reason}"
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise SpheronError(f"{method} {url.split('?')[0]} failed: {exc}") from exc

    def _interpret(self, status: int, raw: bytes, method: str, path: str):
        # Never include the URL query or headers in an error: no key leakage.
        where = f"{method} {path}"
        if status == 429:
            retry_after = None
            try:
                retry_after = float(json.loads(raw).get("retryAfter"))
            except Exception:  # pylint: disable=broad-except
                pass
            raise SpheronRateLimited(
                f"{where}: rate limited (RATE_LIMIT_EXCEEDED); "
                f"retryAfter={retry_after}",
                retry_after_s=retry_after,
            )
        if status in (401, 403):
            raise SpheronAuthError(
                f"{where}: HTTP {status}. Check SPHERON_API_KEY, and note the "
                "API rejects a default python User-Agent."
            )
        if status >= 400:
            detail = ""
            try:
                parsed = json.loads(raw)
                # Documented envelope: {"error": ..., "code": ..., "details": {}}
                code = parsed.get("code")
                message = (
                    parsed.get("error")
                    or parsed.get("error_message")
                    or parsed.get("message")
                    or ""
                )
                detail = f"{code}: {message}" if code else str(message)
                detail = detail[:300]
            except Exception:  # pylint: disable=broad-except
                detail = raw[:200].decode("utf-8", "replace")
            if "balance" in detail.lower() or "credit" in detail.lower():
                raise SpheronInsufficientBalance(f"{where}: {detail}")
            raise SpheronError(f"{where}: HTTP {status}: {detail}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SpheronError(f"{where}: response was not JSON ({exc})") from exc

    # -- account ----------------------------------------------------------

    def list_providers(self) -> List[str]:
        return list(self._call("GET", "/api/providers") or [])

    def get_balance(self) -> Dict[str, Any]:
        return self._call("GET", "/api/balance") or {}

    def current_team_id(self) -> str:
        """Id of the current team.

        REQUIRED on every deployment create, despite the manual (section 6)
        listing ``teamId`` as optional with "Defaults to the current team".
        Measured 2026-09-11: omitting it returns
        ``400: Team ID is required for authenticated deployments``.
        """
        for team in self.get_balance().get("teams") or []:
            if team.get("isCurrentTeam"):
                team_id = team.get("teamId") or team.get("id")
                if team_id:
                    return str(team_id)
        raise SpheronError("no current team found on /api/balance")

    def current_team_balance(self) -> float:
        """Balance on the current team, in USD.

        Zero means every deploy will fail. Callers should check this before a
        provision attempt so the failure is reported as "unfunded" rather than
        "capacity unavailable".
        """
        for team in self.get_balance().get("teams") or []:
            if team.get("isCurrentTeam"):
                return float(team.get("balance") or 0)
        raise SpheronError("no current team found on /api/balance")

    # -- offers -----------------------------------------------------------

    def list_offers(self, *, page_limit: int = 50) -> List[Offer]:
        """All offers across all pages, flattened.

        Includes maintenance-flagged offers; filtering is the caller's choice
        so that "why is this SKU missing" is answerable at one level up.
        """
        offers: List[Offer] = []
        page = 1
        while True:
            payload = (
                self._call(
                    "GET", "/api/gpu-offers", params={"page": page, "limit": page_limit}
                )
                or {}
            )
            for group in payload.get("data", []):
                gpu_type = group.get("gpuType")
                if not gpu_type:
                    raise SpheronError(f"gpu group without gpuType: {sorted(group)}")
                for raw in group.get("offers", []):
                    offers.append(Offer.from_payload(gpu_type, raw))
            total_pages = int(payload.get("totalPages") or 1)
            if page >= total_pages:
                return offers
            page += 1

    # -- ssh keys ---------------------------------------------------------

    def list_ssh_keys(self) -> List[Dict[str, Any]]:
        result = self._call("GET", "/api/ssh-keys")
        if isinstance(result, dict):
            return list(result.get("data") or result.get("sshKeys") or [])
        return list(result or [])

    def add_ssh_key(self, name: str, public_key: str) -> Dict[str, Any]:
        return (
            self._call(
                "POST", "/api/ssh-keys", body={"name": name, "publicKey": public_key}
            )
            or {}
        )

    def ensure_ssh_key(self, name: str, public_key: str) -> str:
        """Return the id of a key matching ``public_key``, adding it if absent.

        Preferring a registered ``sshKeyId`` over an inline key per deploy keeps
        the deploy body small and avoids Spheron minting a throwaway key per
        instance.
        """
        wanted = public_key.strip().split()
        wanted_material = wanted[1] if len(wanted) > 1 else public_key.strip()
        for key in self.list_ssh_keys():
            existing = str(key.get("publicKey") or "").strip().split()
            material = existing[1] if len(existing) > 1 else ""
            if material and material == wanted_material:
                key_id = key.get("id") or key.get("_id")
                if key_id:
                    return str(key_id)
        created = self.add_ssh_key(name, public_key)
        key_id = created.get("id") or created.get("_id")
        if not key_id:
            raise SpheronError(f"POST /api/ssh-keys returned no id: {sorted(created)}")
        return str(key_id)

    # -- deployments ------------------------------------------------------

    def create_deployment(self, request: DeploymentRequest) -> Dict[str, Any]:
        """Create a deployment, resolving ``teamId`` when the caller omitted it.

        ``teamId`` is REQUIRED despite the manual calling it optional, so a
        direct caller that builds a DeploymentRequest without one would always
        get ``400: Team ID is required for authenticated deployments``. The
        provisioner already supplies it; this closes the gap for everyone else.
        (CodeRabbit on #338.)
        """
        body = request.to_body()
        if not body.get("teamId"):
            body["teamId"] = self.current_team_id()
        return self._call("POST", "/api/deployments", body=body) or {}

    def list_deployments(self) -> List[Dict[str, Any]]:
        result = self._call("GET", "/api/deployments")
        if isinstance(result, dict):
            return list(result.get("data") or result.get("deployments") or [])
        return list(result or [])

    def get_deployment(self, deployment_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/api/deployments/{deployment_id}") or {}

    def terminate_deployment(self, deployment_id: str) -> Any:
        return self._call("DELETE", f"/api/deployments/{deployment_id}")

    def can_terminate(self, deployment_id: str) -> Dict[str, Any]:
        """Minimum-runtime check before a terminate.

        The manual is explicit that the 20-minute figure must NOT be assumed:
        ``minimumRuntime`` is the larger of the account minimum and the machine
        type's provider minimum, and a provider minimum is charged in full even
        on an early terminate.
        """
        return (
            self._call("GET", f"/api/deployments/{deployment_id}/can-terminate") or {}
        )

    @staticmethod
    def connection_info(deployment: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """Extract the reachable address from a deployment object.

        Per the manual, ``ipAddress`` and ``sshCommand`` are populated once the
        status is ``running`` -- and only then. Asking earlier yields None,
        which is why the provisioner must wait for `running` before wiring up
        SSH rather than reading the create response.
        """
        status = str(deployment.get("status") or "").lower()
        if status != STATUS_RUNNING:
            return {"status": status, "ip_address": None, "ssh_command": None}
        return {
            "status": status,
            "ip_address": deployment.get("ipAddress"),
            "ssh_command": deployment.get("sshCommand"),
        }

    # -- polling ----------------------------------------------------------

    def wait_until_running(
        self,
        deployment_id: str,
        *,
        timeout_s: float = 1800,
        poll_interval_s: float = 20,
        sleep=time.sleep,
        now=time.monotonic,
    ) -> Dict[str, Any]:
        """Poll until the deployment is running, or fail loudly.

        ``poll_interval_s`` defaults to 20s: the manual prescribes 15-30s and
        forbids faster than 10s, and the quota is shared with the catalog
        fetcher and the controller.
        """
        if poll_interval_s < MIN_POLL_INTERVAL_S:
            raise SpheronError(
                f"poll_interval_s={poll_interval_s} is below the documented "
                f"floor of {MIN_POLL_INTERVAL_S}s"
            )
        deadline = now() + timeout_s
        last_status = None
        while True:
            info = self.get_deployment(deployment_id)
            status = str(info.get("status") or "").lower()
            last_status = status or last_status
            if status == STATUS_RUNNING:
                return info
            if status in _TERMINAL_STATUSES:
                hint = ""
                if status == STATUS_TERMINATED_PROVIDER:
                    # Spot reclamation. Retrying the same SPOT offer will
                    # likely repeat; the caller should fall back to DEDICATED.
                    hint = (
                        " (reclaimed by the provider -- on SPOT this means "
                        "the capacity was interrupted)"
                    )
                raise SpheronError(
                    f"deployment {deployment_id} entered terminal status "
                    f"{status!r} while waiting for running{hint}"
                )
            if now() >= deadline:
                raise SpheronError(
                    f"deployment {deployment_id} still {last_status!r} after "
                    f"{timeout_s:.0f}s"
                )
            sleep(poll_interval_s)
