"""Vast cloud adaptor."""

import functools

_vast_sdk = None

# ISO 3166-1 alpha-2 → georegion token for codes vastai-sdk 0.2.5's
# `_regions_rev` omits. The SDK ships 182 of the 249 officially assigned
# codes; the live marketplace is not limited to that set — Ireland (IE)
# offers appeared 2026-09-15, Macau (MO) on 2026-09-16, each crashing the
# whole catalog fetch with KeyError inside `queryFormatter`. Rather than
# patching one country at a time, this table completes the map: after the
# patch, every officially assigned alpha-2 code resolves.
#
# Tokens are the SDK's own (AF/AS/EU/LC/NA/OC), assigned per the UN M49
# geoscheme; territories with no M49 continent (AQ, BV, GS, HM, TF, UM)
# fall to Oceania by convention. `setdefault` keeps each entry a no-op
# once a newer SDK covers the code natively.
_MISSING_GEOREGIONS = {
    'AF': 'AS',  # Afghanistan
    'AI': 'LC',  # Anguilla
    'AQ': 'OC',  # Antarctica
    'AS': 'OC',  # American Samoa
    'AW': 'LC',  # Aruba
    'AX': 'EU',  # Åland Islands
    'BL': 'LC',  # Saint Barthélemy
    'BM': 'NA',  # Bermuda
    'BQ': 'LC',  # Caribbean Netherlands
    'BV': 'OC',  # Bouvet Island
    'CC': 'AS',  # Cocos (Keeling) Islands
    'CI': 'AF',  # Côte d'Ivoire
    'CK': 'OC',  # Cook Islands
    'CW': 'LC',  # Curaçao
    'CX': 'AS',  # Christmas Island
    'DM': 'LC',  # Dominica
    'EH': 'AF',  # Western Sahara
    'FK': 'LC',  # Falkland Islands
    'FO': 'EU',  # Faroe Islands
    'GD': 'LC',  # Grenada
    'GF': 'LC',  # French Guiana
    'GG': 'EU',  # Guernsey
    'GI': 'EU',  # Gibraltar
    'GL': 'NA',  # Greenland
    'GP': 'LC',  # Guadeloupe
    'GS': 'OC',  # South Georgia and the Islands
    'GT': 'LC',  # Guatemala
    'HM': 'OC',  # Heard & McDonald Islands
    'IE': 'EU',  # Ireland
    'IM': 'EU',  # Isle of Man
    'IO': 'AS',  # British Indian Ocean Territory
    'JE': 'EU',  # Jersey
    'KG': 'AS',  # Kyrgyzstan
    'KN': 'LC',  # Saint Kitts and Nevis
    'KY': 'LC',  # Cayman Islands
    'LA': 'AS',  # Laos
    'LB': 'AS',  # Lebanon
    'LC': 'LC',  # Saint Lucia
    'MF': 'LC',  # Saint Martin (French)
    'MG': 'AF',  # Madagascar
    'MO': 'AS',  # Macau
    'MP': 'OC',  # Northern Mariana Islands
    'MQ': 'LC',  # Martinique
    'MS': 'LC',  # Montserrat
    'NC': 'OC',  # New Caledonia
    'NF': 'OC',  # Norfolk Island
    'NU': 'OC',  # Niue
    'PF': 'OC',  # French Polynesia
    'PM': 'NA',  # Saint Pierre and Miquelon
    'PN': 'OC',  # Pitcairn
    'PS': 'AS',  # Palestine
    'RE': 'AF',  # Réunion
    'SB': 'OC',  # Solomon Islands
    'SJ': 'EU',  # Svalbard & Jan Mayen
    'SM': 'EU',  # San Marino
    'SR': 'LC',  # Suriname
    'SX': 'LC',  # Sint Maarten (Dutch)
    'TC': 'LC',  # Turks and Caicos
    'TF': 'OC',  # French Southern Territories
    'TK': 'OC',  # Tokelau
    'TL': 'AS',  # Timor-Leste
    'UM': 'OC',  # U.S. Minor Outlying Islands
    'UY': 'LC',  # Uruguay
    'UZ': 'AS',  # Uzbekistan
    'VC': 'LC',  # Saint Vincent and the Grenadines
    'VE': 'LC',  # Venezuela
    'VG': 'LC',  # British Virgin Islands
    'VI': 'LC',  # U.S. Virgin Islands
    'WF': 'OC',  # Wallis and Futuna
    'WS': 'OC',  # Samoa
    'YT': 'AF',  # Mayotte
}


def _patch_sdk_regions():
    """Complete vastai-sdk 0.2.5's incomplete ISO 3166-1 reverse map.

    Measured live: the SDK's `queryFormatter` does `_regions_rev[country]`
    for every offer's geolocation and raises KeyError on any code outside
    its 182-entry map, crashing the entire catalog fetch (`fetch_vast.py`
    → 0 GPUs). IE appeared in the marketplace 2026-09-15; MO on
    2026-09-16 — the gap set drifts with the marketplace, so the patch
    completes the map for every officially assigned code instead of
    chasing individual countries.

    `_regions_rev` maps ISO alpha-2 → GEOREGION TOKEN (AF/AS/EU/LC/NA/OC),
    NOT country name — verified: US→NA, DE→EU, JP→AS, AU→OC.

    VERSION-GUARDED, NOT SILENT: this touches a private global in a
    deprecated SDK (renamed to `vastai`). If the SDK is upgraded and no
    longer has `_regions_rev`, the catalog would silently regress to the
    crash — so we RAISE, not no-op. When a newer SDK covers a code
    natively, that code's `setdefault` is a harmless no-op and can be
    dropped from the table.
    """
    from vastai import vastai_sdk
    # Fail loudly if the SDK internals we patch have moved (version drift)
    if not hasattr(vastai_sdk, '_regions_rev'):
        raise RuntimeError(
            'vastai-sdk internal _regions_rev not found — the SDK has '
            'changed shape. The georegion completion shim needs review. '
            'Expected vastai-sdk 0.2.x (pinned 0.2.5 in skypilot-controller).'
        )
    for code, token in _MISSING_GEOREGIONS.items():
        vastai_sdk._regions_rev.setdefault(code, token)


def _patch_sdk_instances_endpoint():
    """Route the instance-LIST call off vast.ai's deprecated v0 endpoint.

    2026-09-16: GET /api/v0/instances (the collection) answers
    `HTTP 410 Gone: "Use /api/v1/instances/ instead"`. vastai-sdk 0.2.5
    swallows the HTTPError with a bare `except: pass` and returns the
    empty-captured-stdout sentinel, so every caller sees an EMPTY
    instance list: `sky status` reports nothing and the provisioner's
    readiness loop polls forever (verified live: instance running with
    ssh_port assigned, poll stuck at `(0/1)`).

    Per-id v0 routes still work (show/create/start/stop/destroy all
    probed alive on both v0 and v1), so the rewrite is scoped to the
    collection call only — subpath == '/instances'.

    VERSION-GUARDED, NOT SILENT: same contract as the georegion patch —
    if `vastai.vast.apiurl` moves or disappears, raise rather than let
    the catalog silently regress to a 410.
    """
    from vastai import vast as _vast_cli
    if not hasattr(_vast_cli, 'apiurl'):
        raise RuntimeError(
            'vastai-sdk internal vast.apiurl not found — the SDK has '
            'changed shape. The v1 instances-endpoint shim needs review. '
            'Expected vastai-sdk 0.2.x (pinned 0.2.5 in '
            'skypilot-controller).')
    if getattr(_vast_cli.apiurl, '_skypilot_v1_instances', False):
        return  # already patched — idempotent
    _orig_apiurl = _vast_cli.apiurl

    def apiurl(args, subpath, query_args=None):
        """Wrap vastai.vast.apiurl; rewrite only the dead collection URL."""
        url = _orig_apiurl(args, subpath, query_args)
        if subpath == '/instances':
            # The collection endpoint is dead on v0; per-id routes stay v0.
            return url.replace('/api/v0/instances?',
                               '/api/v1/instances/?', 1)
        return url

    apiurl._skypilot_v1_instances = True
    _vast_cli.apiurl = apiurl


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _vast_sdk

        if _vast_sdk is None:
            try:
                import vastai_sdk as _vast  # pylint: disable=import-outside-toplevel
                _vast_sdk = _vast.VastAI()
                _patch_sdk_regions()
                _patch_sdk_instances_endpoint()
            except ImportError as e:
                raise ImportError(f'Fail to import dependencies for vast: {e}\n'
                                  'Try pip install "skypilot[vast]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def vast():
    """Return the vast package."""
    return _vast_sdk
