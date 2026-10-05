"""Latitude.sh REST client.

Latitude.sh (https://www.latitude.sh) rents **bare metal** servers — every
server is a real box with systemd and its own OS, addressed as
``ssh root@<primary_ipv4>`` on port 22. There is no VM/container split, no
port mapping and no KVM-image constraint (the constraint that starved the
Vast lane; see the uRun ENG-507 ticket and the Vast carries in
skypilot-controller's ``pyproject.toml``).

The API is JSON:API (https://jsonapi.org) over ``https://api.latitude.sh``:

* Request bodies are ``{"data": {"type": ..., "attributes": {...}}}``.
* Responses are ``application/vnd.api+json``: ``{"data": ..., "meta": ...}``
  on success and ``{"errors": [{"code", "title", "status", "detail",
  "meta"}]}`` on failure. A ``429`` carries ``errors[0].code ==
  "RATE_LIMIT_EXCEEDED"`` and ``errors[0].meta.retry_after`` (seconds).
* Lists paginate with ``page[number]`` / ``page[size]``.
* Server ``status`` is an OPEN SET: steady states are ``on``, ``off``,
  ``unknown``, ``deploying``, ``failed_deployment``, ``disk_erasing``,
  ``rescue_mode``, ``entering_rescue_mode``, ``exiting_rescue_mode``, but
  while provisioning the platform returns its provisioning slug verbatim
  (``queued``, ``starting_deploy``, ``commissioning``, ...) — never
  enumerate it.

Operating rules this client enforces (each one paid for on the Vast lane):

* **Every call has an explicit timeout.** A single hung HTTP call once wedged
  the Vast runner in ``SkyPilotLaunchInFlight`` for 30+ minutes.
* **429 backs off honoring the provider's own ``retry_after``**, bounded —
  rate budgets are 800 GET/min and 120 writes/min per key, and a
  provisioning loop can hit the write cap fast.
* **Never leak the API key**: error messages carry method + path, never the
  URL query, headers, or body.
* **Fail loud, never silently empty**: a payload we cannot interpret raises
  rather than returning a list that would read as "no capacity".
"""

from __future__ import annotations

import dataclasses
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

API_BASE = "https://api.latitude.sh"

# Identifies the integration to the provider's logs.
USER_AGENT = "urun-skypilot-latitude/0.1 (+https://urun.sh)"

# One timeout for every request, including the reads inside poll loops —
# the point is the bound, not the value.
DEFAULT_TIMEOUT_S = 60

# Documented per-key budgets (https://www.latitude.sh/docs/api-reference/
# rate-limits): 800 GET/min, 120 writes/min. Callers budget with these
# instead of discovering a 429 mid-provision.
RATE_LIMIT_READS_PER_MIN = 800
RATE_LIMIT_WRITES_PER_MIN = 120

# 429 handling: honor the provider's retry_after, but never sleep an
# unbounded (or dishonest) amount. Three strikes then raise.
MAX_RATE_LIMIT_RETRIES = 3
MAX_RETRY_AFTER_S = 60.0

# Pagination: loop until a short page; the cap turns a server that ignores
# pagination into a loud error instead of a silently truncated catalog.
PAGE_SIZE = 50
MAX_PAGES = 100

# Server status steady states (the full open set is NOT knowable — see the
# module docstring). Anything not mapped here is transitional.
STATUS_ON = "on"
STATUS_OFF = "off"
STATUS_RESCUE_MODE = "rescue_mode"
STATUS_FAILED_DEPLOYMENT = "failed_deployment"

# The provider's own documented poll cadence for deploys ("10-15s"); going
# faster is pure rate-budget waste.
MIN_POLL_INTERVAL_S = 10

# ~10-minute standard provisioning path plus margin (instant-deployment OSes
# answer in seconds; custom disk layouts take the standard path).
DEFAULT_PROVISION_TIMEOUT_S = 25 * 60

# Environment variable the callers read the key from (the official SDKs name
# theirs LATITUDESH_BEARER; uRun's task secret channel uses this spelling).
API_KEY_ENV = "LATITUDESH_API_KEY"
API_KEY_FILE = "~/.latitude/credentials"


class LatitudeError(RuntimeError):
    """Any Latitude.sh API failure. Never raised with the API key in it."""


class LatitudeAuthError(LatitudeError):
    """401 — key missing, invalid, or revoked. Check/rotate it."""


class LatitudeRateLimited(LatitudeError):
    """429 that exhausted its retries. Carries the last retry_after hint."""

    def __init__(self, message: str, retry_after_s: Optional[float] = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class LatitudeNotFoundError(LatitudeError):
    """404 — the resource is gone. Terminate paths treat this as success."""


def _jsonapi_attributes(resource: Dict[str, Any], where: str) -> Dict[str, Any]:
    attrs = resource.get("attributes")
    if not isinstance(attrs, dict):
        raise LatitudeError(f"{where}: JSON:API resource without attributes")
    return attrs


def _jsonapi_data(payload: Any, where: str) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise LatitudeError(f"{where}: expected a JSON:API data array, got "
                            f"{type(payload).__name__}")
    return payload["data"]

def _jsonapi_object(payload: Any, where: str) -> Dict[str, Any]:
    """A JSON:API single-object envelope, or a loud typed failure.

    `_call(...) or {}` passes a non-empty LIST or STRING through unchanged,
    and the caller's `payload.get("data")` would then die with a bare
    AttributeError — an unclassified exception every LatitudeError-only
    caller misses (CodeRabbit on the PR). A top-level payload that is not
    an object is schema breakage: fail loud, typed.
    """
    if not isinstance(payload, dict):
        raise LatitudeError(
            f"{where}: expected a JSON:API object, got "
            f"{type(payload).__name__}")
    return payload


class LatitudeClient:
    """Thin, explicit client. One method per endpoint we actually use."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = API_BASE,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        transport: Optional[Callable[[str, str, Dict[str, str], Optional[bytes]], Any]] = None,
    ):
        if not api_key or not api_key.strip():
            raise LatitudeAuthError(
                "Latitude.sh API key is empty; refusing to make unauthenticated "
                'calls that would look like "no capacity"'
            )
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        # Injected in tests. Signature: (method, url, headers, payload) -> (status, bytes)
        self._transport = transport

    # -- plumbing ---------------------------------------------------------

    def _http(
        self, method: str, url: str, headers: Dict[str, str], payload: Optional[bytes]
    ):
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                # resp.read() runs OUTSIDE urlopen's URLError handling: a
                # stalled body raises TimeoutError/OSError directly and would
                # escape every caller that only catches LatitudeError (the
                # same trap CodeRabbit flagged on the Spheron client).
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read()
            except (TimeoutError, OSError) as read_exc:
                raise LatitudeError(
                    f"{method} {url.split('?')[0]} failed reading the error "
                    f"body: {read_exc}"
                ) from read_exc
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise LatitudeError(
                f"{method} {url.split('?')[0]} failed: {exc.reason}"
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise LatitudeError(f"{method} {url.split('?')[0]} failed: {exc}") from exc

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
    ) -> Any:
        url = f"{self._base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.api+json",
        }
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/vnd.api+json"

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            status, raw = self._request_once(method, url, headers, payload)
            if status == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                retry_after = _retry_after_s(raw)
                wait_s = min(retry_after if retry_after else 2.0 * (attempt + 1),
                             MAX_RETRY_AFTER_S)
                time.sleep(wait_s)
                continue
            return self._interpret(status, raw, method, path)

        # Unreachable: the loop returns or raises on the final attempt.
        raise AssertionError("rate-limit retry loop exhausted")

    def _interpret(self, status: int, raw: bytes, method: str, path: str):
        # Never include the URL query or headers in an error: no key leakage.
        where = f"{method} {path}"
        if status < 400:
            # The empty-body success path lives UNDER the status check: a
            # 4xx/5xx with an empty body is a FAILURE (raise below), never
            # a silent None — the exact bug class where a provider's
            # 500/401 with an empty body would read as "done" while the
            # server keeps billing (CodeRabbit on the PR).
            if status == 204 or not raw:
                return None
            try:
                return json.loads(raw)
            # ValueError covers JSONDecodeError AND UnicodeDecodeError (a
            # non-UTF-8 gateway page is a ValueError, not a JSONDecodeError)
            # so no raw exception escapes a LatitudeError-only caller.
            except ValueError as exc:
                raise LatitudeError(
                    f"{where}: response was not JSON ({exc})"
                ) from exc
        # Failure: JSON:API errors[] objects.
        errors: List[Dict[str, Any]] = []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and isinstance(parsed.get("errors"), list):
                errors = [e for e in parsed["errors"] if isinstance(e, dict)]
        except (ValueError, AttributeError):
            pass
        first = errors[0] if errors else {}
        detail = str(first.get("detail") or first.get("title") or "")[:300]
        code = str(first.get("code") or "")
        if status == 429:
            raise LatitudeRateLimited(
                f"{where}: rate limited ({code or 'RATE_LIMIT_EXCEEDED'}); "
                f"detail: {detail}",
                retry_after_s=_retry_after_s(raw),
            )
        if status == 401:
            raise LatitudeAuthError(
                f"{where}: HTTP 401. Check {API_KEY_ENV} (or rotate the key in "
                "the Latitude.sh dashboard)."
            )
        if status == 404:
            raise LatitudeNotFoundError(f"{where}: not found ({detail})")
        if errors:
            raise LatitudeError(f"{where}: HTTP {status}: {code}: {detail}")
        raise LatitudeError(
            f"{where}: HTTP {status}: {raw[:200].decode('utf-8', 'replace')}"
        )

    def _paginate(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Collect every ``data`` element across pages, or fail loud.

        Stops on a short page; a server that never shortens a page is cut off
        by ``MAX_PAGES`` as an error, because a truncated silent list is
        exactly how a provider reads as "no capacity".
        """
        out: List[Dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            query = dict(params or {})
            query["page[number]"] = page
            query["page[size]"] = PAGE_SIZE
            payload = self._call("GET", path, params=query) or {}
            rows = _jsonapi_data(payload, f"GET {path} page {page}")
            out.extend(rows)
            if len(rows) < PAGE_SIZE:
                return out
        raise LatitudeError(
            f"GET {path}: more than {MAX_PAGES} pages; refusing to silently "
            "truncate"
        )

    # -- account ----------------------------------------------------------

    def get_profile(self) -> Dict[str, Any]:
        payload = _jsonapi_object(
            self._call("GET", "/user/profile") or {}, "GET /user/profile")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    # -- projects ---------------------------------------------------------

    def list_projects(self, *, slug: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"filter[slug]": slug} if slug else None
        return self._paginate("/projects", params)

    def create_project(self, name: str, description: str = "") -> Dict[str, Any]:
        attributes: Dict[str, Any] = {"name": name}
        if description:
            attributes["description"] = description
        payload = _jsonapi_object(self._call(
            "POST", "/projects",
            body={"data": {"type": "projects", "attributes": attributes}},
        ) or {}, "POST /projects")
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise LatitudeError(f"POST /projects returned no id: {sorted(payload)}")
        return data

    def ensure_project(self, name: str) -> str:
        """The project id for ``name``, creating it if the team has none.

        The project's billing type gates deployment: an On demand project
        accepts ``hourly`` servers (our lane), a Reserved project accepts
        ``yearly``. New projects are On demand by default, so a fresh create
        is always admissible; a pre-existing project with the same name is
        adopted as-is and a reserved one will fail the deploy loudly at
        ``POST /servers`` with the provider's own error body.
        """
        wanted = name.strip().lower()
        for project in self.list_projects():
            attrs = _jsonapi_attributes(project, "GET /projects")
            if (str(attrs.get("slug") or "").lower() == wanted or
                    str(attrs.get("name") or "").lower() == wanted):
                project_id = project.get("id")
                if project_id:
                    return str(project_id)
        return str(self.create_project(name)["id"])

    # -- plans ------------------------------------------------------------

    def list_plans(self) -> List[Dict[str, Any]]:
        """All plans (the provider filters GPU presence server-side).

        Only ``filter[gpu]=true`` is applied: stock, pricing and features
        are interpreted by the catalog fetcher, which must be the one place
        that decides what the lane is willing to rent.
        """
        return self._paginate("/plans", {"filter[gpu]": "true"})

    # -- ssh keys ---------------------------------------------------------

    def list_ssh_keys(self) -> List[Dict[str, Any]]:
        return self._paginate("/ssh_keys")

    def create_ssh_key(self, name: str, public_key: str) -> Dict[str, Any]:
        payload = _jsonapi_object(self._call(
            "POST", "/ssh_keys",
            body={"data": {"type": "ssh_keys",
                           "attributes": {"name": name,
                                          "public_key": public_key}}},
        ) or {}, "POST /ssh_keys")
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise LatitudeError(f"POST /ssh_keys returned no id: {sorted(payload)}")
        return data

    def ensure_ssh_key(self, name: str, public_key: str) -> str:
        """The id of a key matching ``public_key``, adding it if absent.

        Matching on key MATERIAL (not name): a re-created controller keypair
        with the same name must not silently select a stale key, and a
        re-deploy with a renamed key must not mint duplicates.
        """
        wanted = public_key.strip().split()
        wanted_material = wanted[1] if len(wanted) > 1 else public_key.strip()
        for key in self.list_ssh_keys():
            attrs = _jsonapi_attributes(key, "GET /ssh_keys")
            existing = str(attrs.get("public_key") or "").strip().split()
            material = existing[1] if len(existing) > 1 else ""
            if material and material == wanted_material:
                key_id = key.get("id")
                if key_id:
                    return str(key_id)
        return str(self.create_ssh_key(name, public_key)["id"])

    # -- servers ----------------------------------------------------------

    def create_server(
        self,
        *,
        project: str,
        plan: str,
        site: str,
        operating_system: str,
        hostname: str,
        ssh_key_ids: List[str],
        billing: str = "hourly",
    ) -> Dict[str, Any]:
        payload = _jsonapi_object(self._call(
            "POST", "/servers",
            body={"data": {"type": "servers",
                           "attributes": {
                               "project": project,
                               "plan": plan,
                               "site": site,
                               "operating_system": operating_system,
                               "hostname": hostname,
                               "ssh_keys": ssh_key_ids,
                               "billing": billing,
                           }}},
        ) or {}, "POST /servers")
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise LatitudeError(f"POST /servers returned no id: {sorted(payload)}")
        return data

    def get_server(self, server_id: str) -> Dict[str, Any]:
        payload = _jsonapi_object(
            self._call("GET", f"/servers/{server_id}") or {},
            f"GET /servers/{server_id}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LatitudeError(f"GET /servers/{server_id}: no data object")
        return data

    def delete_server(self, server_id: str) -> None:
        """DELETE is the ONLY way to stop hourly billing (a power_off keeps
        the meter running), so terminate paths call this and treat 404 as
        success (idempotent teardown)."""
        self._call("DELETE", f"/servers/{server_id}")

    def list_servers(self, *, hostname: Optional[str] = None) -> List[Dict[str, Any]]:
        """Team servers, optionally filtered by exact hostname.

        The exact-match filter is what reconcile uses — a prefix filter can
        match another cluster whose name merely starts with ours.
        """
        params = {"filter[hostname][eql]": hostname} if hostname else None
        return self._paginate("/servers", params)

    # -- accessors --------------------------------------------------------

    @staticmethod
    def server_status(server: Dict[str, Any]) -> str:
        attrs = _jsonapi_attributes(server, "server")
        return str(attrs.get("status") or "").strip().lower()

    @staticmethod
    def primary_ipv4(server: Dict[str, Any]) -> Optional[str]:
        attrs = _jsonapi_attributes(server, "server")
        ip = attrs.get("primary_ipv4")
        return str(ip) if ip else None

    # -- polling ----------------------------------------------------------

    def wait_until_on(
        self,
        server_id: str,
        *,
        timeout_s: float = DEFAULT_PROVISION_TIMEOUT_S,
        poll_interval_s: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> Dict[str, Any]:
        """Poll until the server is ``on``, or fail closed.

        ``failed_deployment`` raises immediately (never auto-retry — the
        operator decides), and so does the deadline: a stuck provisioning
        slug must become a failed runner, not an endless wait (the exact
        failure mode that kept a Vast instance ``SkyPilotProvisioning`` for
        30 minutes while the provider had already given up).
        """
        if poll_interval_s < MIN_POLL_INTERVAL_S:
            raise LatitudeError(
                f"poll_interval_s={poll_interval_s} is below the documented "
                f"floor of {MIN_POLL_INTERVAL_S}s"
            )
        deadline = now() + timeout_s
        last_status: Optional[str] = None
        while True:
            server = self.get_server(server_id)
            status = self.server_status(server)
            if status:
                last_status = status
            if status == STATUS_ON:
                return server
            if status == STATUS_FAILED_DEPLOYMENT:
                raise LatitudeError(
                    f"server {server_id} entered failed_deployment while "
                    "waiting for on; not auto-retrying (report and decide)"
                )
            if now() >= deadline:
                raise LatitudeError(
                    f"server {server_id} still "
                    f"{last_status or 'provisioning'!r} after "
                    f"{timeout_s:.0f}s; refusing to wait longer"
                )
            sleep(poll_interval_s)


def _retry_after_s(raw: bytes) -> Optional[float]:
    """The provider's own retry hint from a 429 body, if present and sane.

    JSON:API puts it at ``errors[0].meta.retry_after``; a negative or absurd
    value is discarded rather than trusted (we sleep bounded regardless).
    """
    try:
        parsed = json.loads(raw)
        errors = parsed.get("errors")
        if isinstance(errors, list) and errors:
            meta = errors[0].get("meta")
            if isinstance(meta, dict):
                value = meta.get("retry_after")
                if value is not None:
                    value_f = float(value)
                    if 0 < value_f <= 3600:
                        return value_f
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        pass
    return None
