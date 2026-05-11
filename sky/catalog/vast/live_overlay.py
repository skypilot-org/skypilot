"""Vast live-price overlay for the SkyPilot catalog.

The cached `vast/vms.csv` published by the SkyPilot catalog repo is the
source of truth for vast inventory in the SDK; however it can be hours stale,
which means users may not see the cheapest vast price/inventory at the time
they query. This module fetches recent offers from Vast's `/bundles/`
endpoint, normalizes them into the same shape as `vms.csv`, and merges them
into the catalog dataframe at read time.

Design notes:
- Stale-while-revalidate disk cache. Reads never block on the network.
- Fail-open: any error path returns the unmodified base catalog.
- Schema-identical with `vms.csv` so all advanced filters (`datacenter_only`,
  `max_hourly_cost`, region/cpu/memory filters) keep working unchanged.
- SDK-version-agnostic: any `TypeError`/`AttributeError` from a future
  `vastai_sdk` signature drift latches the overlay off for the process.
"""
import json
import os
import tempfile
import threading
import time
import typing
from typing import Any, Dict, List, Optional, Tuple

import filelock

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.catalog.vast import _offer_processing as proc
from sky.utils import annotations

if typing.TYPE_CHECKING:
    import pandas as pd
else:
    pd = adaptors_common.LazyImport('pandas')

logger = sky_logging.init_logger(__name__)


# ---------------------------------------------------------------------------
# Tunables (env vars, read at import time).
# ---------------------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    """Parse an int env var; on bad input, log and fall back to the default.

    The overlay is fail-open; a malformed env var must not break catalog
    loading.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f'vast live overlay: ignoring invalid {name}={raw!r}; '
                       f'using default {default}.')
        return default


_TTL_SEC = _env_int('SKYPILOT_VAST_LIVE_TTL_SEC', 600)
_FETCH_TIMEOUT_SEC = _env_int('SKYPILOT_VAST_LIVE_FETCH_TIMEOUT_SEC', 8)
_DISABLED_BY_ENV = os.environ.get('SKYPILOT_VAST_DISABLE_LIVE_OVERLAY',
                                  '0') == '1'

_CACHE_DIR = os.path.expanduser('~/.sky/cache/vast')
_CACHE_PATH = os.path.join(_CACHE_DIR, 'live_offers.csv')
_META_PATH = os.path.join(_CACHE_DIR, 'live_offers.meta')
_LOCK_PATH = _META_PATH + '.lock'

_QUERY = ('georegion = true chunked = true '
          'inet_down >= 100 disk_space >= 80')

# ---------------------------------------------------------------------------
# Process-local state.
# ---------------------------------------------------------------------------
_REFRESH_LOCK = threading.Lock()
_REFRESH_INFLIGHT = False
_REFRESH_SPAWN_TS = 0.0
_OVERLAY_DISABLED = False  # latched on hard SDK incompatibility
_TEST_REFRESH_DISABLED = False  # set by unit-test fixture; never spawn threads

# Single-entry memo: keyed on (base_mtime, overlay_mtime), replaced on change.
_MEMO: Dict[Tuple[float, float], 'pd.DataFrame'] = {}


def _ensure_cache_dir() -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _is_overlay_active() -> bool:
    return not (_DISABLED_BY_ENV or _OVERLAY_DISABLED)


# ---------------------------------------------------------------------------
# SDK call (defensive).
# ---------------------------------------------------------------------------
def _search_offers_safely() -> Optional[List[Dict[str, Any]]]:
    """Call `vastai_sdk.search_offers` with a wall-clock cap. Never raises.

    Returns None on any failure path (no SDK, signature drift, network
    error, server error, non-list payload).
    """
    global _OVERLAY_DISABLED
    try:
        # pylint: disable=import-outside-toplevel
        from sky.adaptors import vast as vast_adaptor
        sdk = vast_adaptor.vast()
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'vast live overlay: SDK unavailable: {e}')
        return None

    result_box: List[Any] = [None]
    err_box: List[Optional[BaseException]] = [None]

    def _do_call() -> None:
        try:
            result_box[0] = sdk.search_offers(query=_QUERY, limit=10000)
        except BaseException as exc:  # pylint: disable=broad-except
            err_box[0] = exc

    worker = threading.Thread(target=_do_call, daemon=True)
    worker.start()
    worker.join(_FETCH_TIMEOUT_SEC)
    if worker.is_alive():
        logger.debug('vast live overlay: search_offers timed out')
        return None

    err = err_box[0]
    if isinstance(err, (TypeError, AttributeError)):
        _OVERLAY_DISABLED = True
        logger.warning(f'vast live overlay disabled: SDK incompatible: {err}')
        return None
    if err is not None:
        logger.debug(f'vast live overlay: search_offers failed: {err}')
        return None

    result = result_box[0]
    if not isinstance(result, list):
        # The SDK returns int on some error paths (see provision/vast/utils.py).
        return None
    return result


# ---------------------------------------------------------------------------
# Disk cache I/O.
# ---------------------------------------------------------------------------
def _write_meta_atomically(meta: Dict[str, Any]) -> None:
    _ensure_cache_dir()
    with tempfile.NamedTemporaryFile(mode='w',
                                     dir=_CACHE_DIR,
                                     delete=False,
                                     encoding='utf-8') as tmp:
        json.dump(meta, tmp)
        tmp_path = tmp.name
    os.rename(tmp_path, _META_PATH)


def _read_meta() -> Optional[Dict[str, Any]]:
    try:
        with open(_META_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache_atomically(df: 'pd.DataFrame', offer_count: int) -> None:
    _ensure_cache_dir()
    with tempfile.NamedTemporaryFile(mode='w',
                                     dir=_CACHE_DIR,
                                     delete=False,
                                     newline='',
                                     encoding='utf-8') as tmp:
        df.to_csv(tmp, index=False)
        tmp_path = tmp.name
    os.rename(tmp_path, _CACHE_PATH)
    _write_meta_atomically({
        'ts': time.time(),
        'status': 'ok',
        'offer_count': offer_count,
        'row_count': int(df.shape[0]),
    })


def _read_cached_df() -> Optional['pd.DataFrame']:
    if not os.path.exists(_CACHE_PATH):
        return None
    try:
        df = pd.read_csv(_CACHE_PATH)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'vast live overlay: cache read failed: {e}')
        return None
    if df.empty:
        return None
    return df


def _cache_age_sec() -> Optional[float]:
    try:
        mtime = os.path.getmtime(_CACHE_PATH)
    except OSError:
        return None
    return time.time() - mtime


# ---------------------------------------------------------------------------
# Background refresh.
# ---------------------------------------------------------------------------
def _refresh_locked() -> None:
    """Write a fresh overlay CSV. Caller holds the cross-process file lock."""
    age = _cache_age_sec()
    if age is not None and age < _TTL_SEC:
        # Another process won the race.
        return
    # Back off if a recent fetch failed — otherwise a Vast outage causes
    # every catalog read to re-fire the SDK once the inflight watchdog
    # window elapses.
    if _recent_fetch_failed():
        return
    offers = _search_offers_safely()
    if offers is None:
        # Mark the failure so subsequent refreshes back off; the existing
        # CSV is left untouched.
        try:
            _write_meta_atomically({
                'ts': time.time(),
                'status': 'fetch_failed',
            })
        except OSError:
            pass
        return
    df = proc.offers_to_dataframe(offers)
    if df.empty:
        return
    _write_cache_atomically(df, offer_count=len(offers))


def _refresh_safely() -> None:
    global _REFRESH_INFLIGHT
    try:
        _ensure_cache_dir()
        with filelock.FileLock(_LOCK_PATH, timeout=0.1):
            _refresh_locked()
    except filelock.Timeout:
        # Another process is refreshing; that's fine.
        pass
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'vast live overlay: refresh raised: {e}')
    finally:
        with _REFRESH_LOCK:
            _REFRESH_INFLIGHT = False


def _recent_fetch_failed() -> bool:
    """True if the last refresh wrote status=fetch_failed within the TTL.

    Used to back off so a Vast outage doesn't cause every catalog read to
    spawn a thread that immediately bails inside the file lock.
    """
    meta = _read_meta()
    if meta is None or meta.get('status') != 'fetch_failed':
        return False
    return time.time() - meta.get('ts', 0) < _TTL_SEC


def _maybe_spawn_refresh() -> None:
    """Spawn a single background refresh if none is in flight.

    Includes a watchdog so a hung SDK call doesn't permanently disable
    refreshes for the life of the process. Backs off when the previous
    fetch failed recently.
    """
    global _REFRESH_INFLIGHT, _REFRESH_SPAWN_TS
    if _TEST_REFRESH_DISABLED:
        return
    if _recent_fetch_failed():
        return
    with _REFRESH_LOCK:
        now = time.time()
        if _REFRESH_INFLIGHT:
            if now - _REFRESH_SPAWN_TS < 2 * _FETCH_TIMEOUT_SEC:
                return
            # Watchdog: assume the prior thread is wedged; allow respawn.
            logger.debug('vast live overlay: refresh watchdog tripped')
        _REFRESH_INFLIGHT = True
        _REFRESH_SPAWN_TS = now
    threading.Thread(target=_refresh_safely, daemon=True).start()


# ---------------------------------------------------------------------------
# Public surface.
# ---------------------------------------------------------------------------
def get_overlay_dataframe() -> Optional['pd.DataFrame']:
    """Return the latest disk-cached overlay df, or None.

    Spawns a background refresh when the cache is stale or missing. Never
    raises.
    """
    if not _is_overlay_active():
        return None
    age = _cache_age_sec()
    if age is None:
        # Cold start: skip overlay this call; warm the cache for next time.
        _maybe_spawn_refresh()
        return None
    if age > _TTL_SEC:
        _maybe_spawn_refresh()
    return _read_cached_df()


def _dedupe_overlay(df: 'pd.DataFrame', keys: List[str]) -> 'pd.DataFrame':
    """Collapse overlay rows sharing `keys`.

    Multiple Vast offers can normalize to the same (InstanceType, Region,
    HostingType) tuple. Left untouched, the outer merge in `merge_with_base`
    multiply-matches base rows and silently inflates the catalog. We take
    the min real (>0) Price/SpotPrice and the first value for other columns.
    """
    if not df.duplicated(subset=keys).any():
        return df
    df = df.copy()
    for col in ('Price', 'SpotPrice'):
        df[col] = pd.to_numeric(df[col],
                                errors='coerce').replace(0, float('nan'))
    agg: Dict[str, str] = {c: 'first' for c in df.columns if c not in keys}
    agg['Price'] = 'min'
    agg['SpotPrice'] = 'min'
    df = df.groupby(keys, as_index=False).agg(agg)
    df['Price'] = df['Price'].fillna(0.0)
    df['SpotPrice'] = df['SpotPrice'].fillna(0.0)
    return df


def merge_with_base(base_df: 'pd.DataFrame',
                    overlay_df: Optional['pd.DataFrame']) -> 'pd.DataFrame':
    """Min-price merge of overlay into base on (InstanceType, Region,
    HostingType).

    - both: Price = min over real (>0) values; SpotPrice same.
    - csv-only: keep CSV row.
    - live-only: append (so new GPU types surface immediately).

    Pure function. Returns base_df unchanged on any schema mismatch.
    """
    if overlay_df is None or overlay_df.empty:
        return base_df

    csv_cols: List[str] = list(base_df.columns)
    required = {'InstanceType', 'Region', 'HostingType', 'Price', 'SpotPrice'}
    if not required.issubset(set(overlay_df.columns)):
        return base_df
    if not required.issubset(set(csv_cols)):
        return base_df

    keys = ['InstanceType', 'Region', 'HostingType']
    overlay_df = _dedupe_overlay(overlay_df, keys)
    overlay_keep = overlay_df[keys + ['Price', 'SpotPrice']].copy()
    overlay_keep = overlay_keep.rename(columns={
        'Price': '_live_price',
        'SpotPrice': '_live_spot',
    })

    merged = base_df.merge(overlay_keep, on=keys, how='outer', indicator=True)

    csv_only = merged['_merge'] == 'left_only'
    both = merged['_merge'] == 'both'
    live_only_mask = merged['_merge'] == 'right_only'

    # both: take min over real (>0) prices.
    if both.any():
        for col, live_col in (('Price', '_live_price'), ('SpotPrice',
                                                         '_live_spot')):
            p_csv = pd.to_numeric(merged.loc[both, col],
                                  errors='coerce').replace(0, float('nan'))
            p_live = pd.to_numeric(merged.loc[both, live_col],
                                   errors='coerce').replace(0, float('nan'))
            merged.loc[both, col] = pd.concat([p_csv, p_live],
                                              axis=1).min(axis=1).fillna(0.0)

    # csv-only: nothing to do; CSV columns are already present.
    del csv_only  # kept for readability

    # live-only: pull full rows from overlay_df. Columns absent from overlay
    # (e.g. AvailabilityZone) are filled with NaN in the concat.
    if live_only_mask.any():
        live_only_keys = merged.loc[live_only_mask, keys]
        live_rows = overlay_df.merge(live_only_keys, on=keys, how='inner')
        # Align columns to base_df's order; missing columns default to NaN.
        for col in csv_cols:
            if col not in live_rows.columns:
                live_rows[col] = float('nan')
        live_rows = live_rows[csv_cols]
    else:
        live_rows = None

    keep_mask = merged['_merge'] != 'right_only'
    base_keep = merged.loc[keep_mask, csv_cols]

    if live_rows is None or live_rows.empty:
        return base_keep.reset_index(drop=True)
    return pd.concat([base_keep, live_rows],
                     ignore_index=True).reset_index(drop=True)


def get_merged_dataframe(base_lazy) -> 'pd.DataFrame':
    """Return a (cached) merge of the base catalog with the live overlay.

    `base_lazy` is a `sky.catalog.common.LazyDataFrame`. We delegate the
    base load through its public `get_dataframe()` so its own staleness
    handling stays intact.
    """
    base_df = base_lazy.get_dataframe()
    if not _is_overlay_active():
        return base_df

    overlay_df = get_overlay_dataframe()
    base_path = base_lazy.filename
    try:
        base_mtime = os.path.getmtime(base_path)
    except OSError:
        base_mtime = 0.0
    try:
        overlay_mtime = os.path.getmtime(_CACHE_PATH)
    except OSError:
        overlay_mtime = 0.0

    key = (base_mtime, overlay_mtime)
    cached = _MEMO.get(key)
    if cached is not None:
        return cached

    try:
        merged = merge_with_base(base_df, overlay_df)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'vast live overlay: merge failed: {e}')
        return base_df
    # Single-entry memo: replaced whenever either mtime changes.
    _MEMO.clear()
    _MEMO[key] = merged
    return merged


# ---------------------------------------------------------------------------
# DataFrame wrapper used by vast_catalog.py.
# ---------------------------------------------------------------------------
class OverlayDataFrame:
    """Drop-in replacement for `LazyDataFrame` that splices live prices.

    Exposes the same `__getattr__` / `__getitem__` / `__setitem__` surface
    that callers in `sky/catalog/common.py` use, so this is invisible to
    every consumer of `vast_catalog._df`.
    """

    def __init__(self, base_lazy):
        self._base = base_lazy

    # Snapshot the merged dataframe once per request. This makes patterns
    # like `df[df['HostingType'] >= 1]` safe: the boolean mask produced by
    # the first call is indexed against the same frame the second call
    # operates on, even if the background refresh writes a new overlay
    # between them.
    @annotations.lru_cache(scope='request')
    def _df(self) -> 'pd.DataFrame':
        return get_merged_dataframe(self._base)

    def __getattr__(self, name: str):
        # Avoid infinite recursion on internal attrs during init.
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self._df(), name)

    def __getitem__(self, key):
        return self._df()[key]

    def __setitem__(self, key, value):
        # The catalog read path does `df = df.copy()` before mutating, so
        # this mostly exists for API parity with LazyDataFrame.
        self._df()[key] = value
