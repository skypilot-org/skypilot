"""Vast.ai cloud adaptor.

A small REST client for the Vast.ai API (https://console.vast.ai/api/v0)
covering the calls SkyPilot makes. SkyPilot used to go through the
``vastai-sdk`` package, but that package's dependency chain pulls in
``borb``, an AGPL-licensed PDF library SkyPilot never uses, so the handful
of endpoints are implemented here directly on top of ``requests``.

The offer query syntax accepted by :meth:`VastClient.search_offers` (e.g.
``num_gpus=1 gpu_name="RTX 4090" cpu_ram>=32``) and the ``georegion`` /
``chunked`` post-processing of offers follow the Vast.ai SDK (MIT), so the
catalog fetcher and the provisioner keep matching offers the same way.
"""
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

DEFAULT_SERVER_URL = 'https://console.vast.ai'
API_KEY_ENV_VAR = 'VAST_API_KEY'
API_KEY_PATH = '~/.config/vastai/vast_api_key'
LEGACY_API_KEY_PATH = '~/.vast_api_key'

_RETRYABLE_STATUS = {429, 502, 503, 504}
_DEFAULT_TIMEOUT_SECONDS = 60
_DEFAULT_RETRIES = 3

_client: Optional['VastClient'] = None
_client_lock = threading.Lock()


def vast() -> 'VastClient':
    """Returns the process-wide Vast.ai API client."""
    global _client
    with _client_lock:
        if _client is None:
            _client = VastClient()
        return _client


def resolve_api_key() -> Optional[str]:
    """Finds the Vast.ai API key the same way the official tooling does.

    Order: ``VAST_API_KEY`` env var, ``$XDG_CONFIG_HOME/vastai/vast_api_key``
    (default ``~/.config/vastai/vast_api_key``), then the legacy
    ``~/.vast_api_key``.
    """
    env_key = os.environ.get(API_KEY_ENV_VAR)
    if env_key:
        return env_key.strip()
    xdg_home = os.environ.get('XDG_CONFIG_HOME')
    candidates = []
    if xdg_home:
        candidates.append(os.path.join(xdg_home, 'vastai', 'vast_api_key'))
    candidates.append(os.path.expanduser(API_KEY_PATH))
    candidates.append(os.path.expanduser(LEGACY_API_KEY_PATH))
    for path in candidates:
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                key = f.read().strip()
            if key:
                return key
    return None


# ---------------------------------------------------------------------------
# Offer query parsing
# ---------------------------------------------------------------------------

# Operators accepted in a query clause, mapped to the API's operator names.
_OPERATORS = {
    '>=': 'gte',
    '>': 'gt',
    'gt': 'gt',
    'gte': 'gte',
    '<=': 'lte',
    '<': 'lt',
    'lt': 'lt',
    'lte': 'lte',
    '!=': 'neq',
    '==': 'eq',
    '=': 'eq',
    'eq': 'eq',
    'neq': 'neq',
    'not eq': 'neq',
    'noteq': 'neq',
    'not in': 'notin',
    'notin': 'notin',
    'nin': 'notin',
    'in': 'in',
}

# One clause: ``field op value``. Values may be quoted (``"RTX 4090"``), a
# bracketed list (``[CA,US]``) or a bare token.
_CLAUSE_RE = re.compile(
    r'\s*(?P<field>[A-Za-z0-9_]+)\s*'
    r'(?P<op>>=|<=|!=|==|=|>|<|'
    r'(?:not\s+in|not\s*eq|notin|nin|neq|gte|gt|lte|lt|eq|in)\b)'
    r'\s*(?P<value>"[^"]*"|\[[^\]]*\]|\S+)\s*')

# Field spellings the API accepts under a different name.
_FIELD_ALIASES = {
    'cuda_vers': 'cuda_max_good',
    'display_active': 'gpu_display_active',
    'dlperf_usd': 'dlperf_per_dphtotal',
    'dph': 'dph_total',
    'flops_usd': 'flops_per_dphtotal',
}

# Fields whose human-facing unit differs from the API's unit.
_FIELD_MULTIPLIERS = {
    'cpu_ram': 1000,
    'gpu_ram': 1000,
    'gpu_total_ram': 1000,
    'duration': 24.0 * 60.0 * 60.0,
}

# Directives handled client-side rather than sent to the API.
_DIRECTIVES = ('georegion', 'chunked')

# Filters applied unless the query overrides them (``verified=any`` etc.).
_DEFAULT_FILTERS: Dict[str, Dict[str, Any]] = {
    'verified': {
        'eq': True
    },
    'external': {
        'eq': False
    },
    'rentable': {
        'eq': True
    },
}

# Continent code -> comma-separated country codes, as used by ``georegion``.
# Same table as the Vast.ai SDK so region strings in the catalog line up.
_REGIONS = {
    'AF': ('DZ,AO,BJ,BW,BF,BI,CM,CV,CF,TD,KM,CD,CG,DJ,EG,GQ,ER,ET,GA,GM,GH,GN,'
           'GW,KE,LS,LR,LY,MW,MA,ML,MR,MU,MZ,NA,NE,NG,RW,SH,ST,SN,SC,SL,SO,ZA,'
           'SS,SD,SZ,TZ,TG,TN,UG,YE,ZM,ZW'),
    'AS': ('AE,AM,AR,AU,AZ,BD,BH,BN,BT,MM,KH,KP,IN,ID,IR,IQ,IL,JP,JO,KZ,LV,'
           'LI,MY,MV,MN,NP,KR,PK,PH,QA,SA,SG,LK,SY,TW,TJ,TH,TR,TM,VN,YE,HK,'
           'CN,OM'),
    'EU': ('AL,AD,AT,BY,BE,BA,BG,HR,CY,CZ,DK,EE,'
           'FI,FR,GE,DE,GR,HU,IS,IT,KZ,LV,LI,LT,'
           'LU,MT,MD,MC,ME,NL,NO,PL,PT,RO,RU,RS,'
           'SK,SI,ES,SE,CH,UA,GB,VA,MK'),
    'LC': ('AG,AR,BS,BB,BZ,BO,BR,CL,CO,CR,CU,DO,EC,SV,GY,HT,HN,JM,MX,NI,PA,PY,'
           'PE,PR,RD,SUR,TT,UR,VZ'),
    'NA': 'CA,US',
    'OC': 'AU,FJ,GU,KI,MH,FM,NR,NZ,PG,PW,SL,TO,TV,VU',
}
_COUNTRY_TO_REGION = {
    country: region for region, countries in _REGIONS.items()
    for country in countries.split(',')
}

# ``chunked`` rounds offers down to a coarse "instance type" so the snowflake
# machines on the marketplace become interchangeable for the catalog.
_CHUNKED_CUTOFFS = {
    'cpu_ram': 64 * 1024,
    'cpu_cores': 32,
    'min_bid': 0,
}


def _coerce_scalar(field: str, value: str) -> Any:
    value = value.strip('"').replace('_', ' ')
    if field in _FIELD_MULTIPLIERS:
        return float(value) * _FIELD_MULTIPLIERS[field]
    if value in ('true', 'True'):
        return True
    if value in ('false', 'False'):
        return False
    if value in ('None', 'null'):
        return None
    return value


def parse_offer_query(
    query: Optional[str],
    defaults: Optional[Dict[str, Dict[str, Any]]] = None
) -> Tuple[Dict[str, Dict[str, Any]], bool, bool]:
    """Parses an offer query string into API filters.

    Example: ``'num_gpus=1 gpu_name="RTX 4090" cpu_ram>=32'`` ->
    ``{'num_gpus': {'eq': '1'}, 'gpu_name': {'eq': 'RTX 4090'},
    'cpu_ram': {'gte': 32000.0}, ...defaults}``.

    Returns:
        ``(filters, georegion, chunked)``: the filters to send to the API and
        whether the ``georegion=true`` / ``chunked=true`` directives were set.

    Raises:
        ValueError: If the query cannot be parsed.
    """
    filters: Dict[str, Dict[str, Any]] = {
        field: dict(ops) for field, ops in (
            _DEFAULT_FILTERS if defaults is None else defaults).items()
    }
    georegion = False
    chunked = False
    if query is None:
        return filters, georegion, chunked

    clauses: List[Tuple[str, str, str]] = []
    pos = 0
    query = query.strip()
    while pos < len(query):
        match = _CLAUSE_RE.match(query, pos)
        if match is None:
            raise ValueError('Cannot parse Vast offer query near '
                             f'{query[pos:]!r} (full query: {query!r}). '
                             'Quote values containing spaces.')
        pos = match.end()
        clauses.append(
            (match.group('field'), match.group('op'), match.group('value')))

    for field, op, raw_value in clauses:
        op_name = _OPERATORS[re.sub(r'\s+', ' ', op.strip())]
        if field in _DIRECTIVES:
            if op_name != 'eq':
                raise ValueError(f'{field} only supports "=": {raw_value!r}')
            enabled = raw_value.strip('"').lower() == 'true'
            if field == 'georegion':
                georegion = enabled
            else:
                chunked = enabled
            continue
        field = _FIELD_ALIASES.get(field, field)
        raw_value = raw_value.strip('[]')
        if not raw_value.strip('"'):
            raise ValueError(f'Empty value for {field!r} in query {query!r}')

        value: Any
        if op_name in ('in', 'notin'):
            value = [
                _coerce_scalar(field, v)
                for v in raw_value.split(',')
                if v.strip()
            ]
        else:
            if raw_value.strip('"') in ('?', '*', 'any'):
                if op_name != 'eq':
                    raise ValueError('Wildcard values only make sense with '
                                     f'"=": {field} {op} {raw_value}')
                filters.pop(field, None)
                continue
            value = _coerce_scalar(field, raw_value)
            if (georegion and field == 'geolocation' and op_name == 'eq' and
                    isinstance(value, str) and value in _REGIONS):
                # geolocation=NA -> geolocation in [CA, US]
                op_name = 'in'
                value = _REGIONS[value].split(',')
        filters.setdefault(field, {})[op_name] = value
    return filters, georegion, chunked


def _parse_order(order: str) -> List[List[str]]:
    """``'score-,dph'`` -> ``[['score', 'desc'], ['dph_total', 'asc']]``."""
    parsed = []
    for name in order.split(','):
        name = name.strip()
        if not name:
            continue
        direction = 'asc'
        if name.strip('-') != name:
            direction = 'desc'
            name = name.strip('-')
        if name.strip('+') != name:
            direction = 'asc'
            name = name.strip('+')
        parsed.append([_FIELD_ALIASES.get(name, name), direction])
    return parsed


def postprocess_offers(offers: List[Dict[str, Any]],
                       georegion: bool = False,
                       chunked: bool = False) -> List[Dict[str, Any]]:
    """Applies the client-side ``georegion`` / ``chunked`` transforms.

    * Always adds a ``datacenter`` boolean derived from ``hosting_type``.
    * ``georegion``: appends the continent code to ``geolocation``
      (``'Texas, US'`` -> ``'Texas, US, NA'``).
    * ``chunked``: drops offers below the cutoffs in ``_CHUNKED_CUTOFFS``,
      pins those fields to the cutoff and rounds ``gpu_ram`` /
      ``disk_space`` down so offers collapse into a few "instance types".
    """
    result = []
    for offer in offers:
        offer['datacenter'] = offer.get('hosting_type') == 1
        if georegion:
            geolocation = offer.get('geolocation') or ''
            if geolocation:
                region = _COUNTRY_TO_REGION.get(geolocation[-2:])
                if region:
                    offer['geolocation'] = f'{geolocation}, {region}'
        if chunked:
            try:
                below_cutoff = any(
                    offer.get(key) is not None and offer[key] < cutoff
                    for key, cutoff in _CHUNKED_CUTOFFS.items())
            except TypeError:
                below_cutoff = True
            if below_cutoff:
                continue
            for key, cutoff in _CHUNKED_CUTOFFS.items():
                offer[key] = cutoff
            offer['gpu_ram'] = int(offer.get('gpu_ram') or 0) & 0xffffffffff0
            offer['disk_space'] = (int(offer.get('disk_space') or 0) &
                                   0xffffffffffc0)
        result.append(offer)
    return result


def _parse_env_string(env: str) -> Dict[str, str]:
    """Parses the ``-e KEY=VALUE -p 8080:8080`` style env string.

    Mirrors the Vast.ai CLI: ``-e`` sets an environment variable, ``-p``
    exposes a port, ``-v`` mounts a volume, ``-n`` names a network and ``-h``
    sets the hostname; each becomes a key in the ``env`` dict the API takes.
    """
    result: Dict[str, str] = {}
    tokens = env.split()
    flag: Optional[str] = None
    for token in tokens:
        if flag is None:
            if token in ('-e', '-p', '-h', '-v', '-n'):
                flag = token
            continue
        if flag == '-e':
            key, sep, value = token.partition('=')
            if sep:
                result[key] = value.strip('\'"')
        elif flag == '-p':
            if set(token) <= set('0123456789:tcp/udp'):
                result[f'-p {token}'] = '1'
        elif flag == '-v':
            if re.fullmatch(r'[A-Za-z0-9:./_]+', token):
                result[f'-v {token}'] = '1'
        elif flag == '-n':
            if re.fullmatch(r'[a-z0-9-]+', token):
                result[f'-n {token}'] = '1'
        else:
            result[flag] = token
        flag = None
    return result


def _strip_jupyter_portal_config(env: Dict[str, str],
                                 runtype: Optional[str]) -> Dict[str, str]:
    """Drops Jupyter entries from PORTAL_CONFIG on non-Jupyter runtypes."""
    if not runtype or 'jupyter' in runtype or 'PORTAL_CONFIG' not in env:
        return env
    entries = [
        entry for entry in env['PORTAL_CONFIG'].split('|')
        if 'jupyter' not in entry.lower()
    ]
    if not entries:
        raise ValueError('PORTAL_CONFIG must contain at least one non-jupyter '
                         'entry when the runtype is not jupyter.')
    env = dict(env)
    env['PORTAL_CONFIG'] = '|'.join(entries)
    return env


class VastClient:
    """Minimal Vast.ai REST API client."""

    def __init__(self,
                 api_key: Optional[str] = None,
                 server_url: str = DEFAULT_SERVER_URL,
                 retries: int = _DEFAULT_RETRIES,
                 timeout: float = _DEFAULT_TIMEOUT_SECONDS):
        if api_key is None:
            api_key = resolve_api_key()
        if not api_key:
            raise RuntimeError(
                'No Vast.ai API key found. Set the '
                f'{API_KEY_ENV_VAR} environment variable or save the key to '
                f'{API_KEY_PATH}.')
        self.api_key = api_key
        self.server_url = server_url.rstrip('/')
        self.retries = max(1, retries)
        self.timeout = timeout

    # -- HTTP plumbing ------------------------------------------------------

    def _request(self,
                 method: str,
                 path: str,
                 params: Optional[Dict[str, str]] = None,
                 json_body: Optional[Any] = None) -> requests.Response:
        """Sends a request, retrying transient failures with backoff.

        Raises:
            requests.HTTPError: For non-2xx responses (after retries).
        """
        if path.startswith('/api/'):
            url = f'{self.server_url}{path}'
        else:
            url = f'{self.server_url}/api/v0{path}'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'User-Agent': 'skypilot',
        }
        backoff = 0.15
        response: Optional[requests.Response] = None
        for attempt in range(self.retries):
            last_attempt = attempt == self.retries - 1
            try:
                response = requests.request(method,
                                            url,
                                            headers=headers,
                                            params=params,
                                            json=json_body,
                                            timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout):
                if last_attempt:
                    raise
                time.sleep(backoff)
                backoff *= 1.5
                continue
            if response.status_code in _RETRYABLE_STATUS and not last_attempt:
                time.sleep(backoff)
                backoff *= 1.5
                continue
            break
        assert response is not None
        response.raise_for_status()
        return response

    # -- SSH keys -----------------------------------------------------------

    def show_ssh_keys(self) -> List[Dict[str, Any]]:
        """Lists the SSH public keys registered on the account."""
        return self._request('GET', '/ssh/').json()

    def create_ssh_key(self, ssh_key: str) -> Dict[str, Any]:
        """Registers an SSH public key on the account."""
        return self._request('POST', '/ssh/', json_body={
            'ssh_key': ssh_key
        }).json()

    # -- Offers -------------------------------------------------------------

    def search_offers(self,
                      query: Optional[str] = None,
                      limit: Optional[int] = None,
                      offer_type: str = 'on-demand',
                      order: str = 'score-',
                      storage: float = 5.0) -> List[Dict[str, Any]]:
        """Searches rentable offers (the ``vastai search offers`` query).

        Args:
            query: Space separated ``field op value`` clauses, e.g.
                ``'num_gpus=1 gpu_name="RTX 4090" disk_space>=100'``. The
                ``georegion=true`` and ``chunked=true`` directives are handled
                client-side (see :func:`postprocess_offers`).
            limit: Maximum number of offers to return.
            offer_type: ``'on-demand'``, ``'reserved'`` or ``'bid'``.
            order: Comma separated sort fields; a trailing ``-`` sorts
                descending.
            storage: Allocated storage (GiB) used for pricing.
        """
        filters, georegion, chunked = parse_offer_query(query)
        body: Dict[str, Any] = dict(filters)
        body['order'] = _parse_order(order)
        body['type'] = 'bid' if offer_type == 'interruptible' else offer_type
        if limit:
            body['limit'] = int(limit)
        body['allocated_storage'] = storage
        offers = self._request('POST', '/bundles/', json_body=body).json()
        return postprocess_offers(offers['offers'],
                                  georegion=georegion,
                                  chunked=chunked)

    # -- Instances ----------------------------------------------------------

    @staticmethod
    def _decorate_instance(row: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(row.get('start_date'), (int, float)):
            row['duration'] = time.time() - row['start_date']
        extra_env = row.get('extra_env')
        if isinstance(extra_env, list):
            row['extra_env'] = {
                pair[0]: pair[1]
                for pair in extra_env
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            }
        return row

    def show_instances(self) -> List[Dict[str, Any]]:
        """Lists the account's instances."""
        rows = self._request('GET', '/instances/', params={
            'owner': 'me'
        }).json()['instances']
        return [self._decorate_instance(row) for row in rows]

    def show_instance(self,
                      instance_id: Union[int, str]) -> Optional[Dict[str, Any]]:
        """Returns one instance, or ``None`` if it does not exist."""
        row = self._request('GET',
                            f'/instances/{instance_id}/',
                            params={
                                'owner': 'me'
                            }).json()['instances']
        if row is None:
            return None
        return self._decorate_instance(row)

    def create_instance(
        self,
        offer_id: Union[int, str],
        *,
        image: Optional[str] = None,
        disk: Optional[float] = 10,
        env: Optional[Union[str, Dict[str, str]]] = None,
        price: Optional[float] = None,
        bid_price: Optional[float] = None,
        label: Optional[str] = None,
        extra: Optional[str] = None,
        onstart_cmd: Optional[str] = None,
        login: Optional[str] = None,
        python_utf8: bool = False,
        lang_utf8: bool = False,
        jupyter_lab: bool = False,
        jupyter_dir: Optional[str] = None,
        force: bool = False,
        cancel_unavail: bool = False,
        template_hash: Optional[str] = None,
        template_hash_id: Optional[str] = None,
        user: Optional[str] = None,
        runtype: Optional[str] = None,
        args: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Rents an offer (the ``vastai create instance`` command).

        Returns the API response; ``response['new_contract']`` is the id of
        the created instance.
        """
        if price is None:
            price = bid_price
        if template_hash is None:
            template_hash = template_hash_id
        env_dict = (_parse_env_string(env)
                    if isinstance(env, str) else dict(env or {}))

        if template_hash is None and runtype is None:
            # Same defaults as the Vast.ai CLI: SSH access unless the caller
            # asks for a raw args container or Jupyter.
            if args is not None:
                runtype = 'args'
            elif jupyter_lab or jupyter_dir:
                runtype = 'jupyter_proxy ssh_proxy'
            else:
                runtype = 'ssh'
        env_dict = _strip_jupyter_portal_config(env_dict, runtype)

        body: Dict[str, Any] = {
            'client_id': 'me',
            'image': image,
            'env': env_dict,
            'price': price,
            'disk': disk,
            'label': label,
            'extra': extra,
            'onstart': onstart_cmd,
            'image_login': login,
            'python_utf8': python_utf8,
            'lang_utf8': lang_utf8,
            'use_jupyter_lab': jupyter_lab,
            'jupyter_dir': jupyter_dir,
            'force': force,
            'cancel_unavail': cancel_unavail,
            'template_hash_id': template_hash,
            'user': user,
        }
        if runtype is not None:
            body['runtype'] = runtype
        if args is not None:
            body['args'] = args
        return self._request('PUT', f'/asks/{offer_id}/', json_body=body).json()

    def start_instance(self, instance_id: Union[int, str]) -> Dict[str, Any]:
        """Starts a stopped instance."""
        return self._request('PUT',
                             f'/instances/{instance_id}/',
                             json_body={
                                 'state': 'running'
                             }).json()

    def stop_instance(self, instance_id: Union[int, str]) -> Dict[str, Any]:
        """Stops a running instance (keeps its disk)."""
        return self._request('PUT',
                             f'/instances/{instance_id}/',
                             json_body={
                                 'state': 'stopped'
                             }).json()

    def destroy_instance(self, instance_id: Union[int, str]) -> Dict[str, Any]:
        """Destroys an instance (irreversible)."""
        return self._request('DELETE',
                             f'/instances/{instance_id}/',
                             json_body={}).json()
