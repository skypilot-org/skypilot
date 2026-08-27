"""Unit tests for the fields-aware /api/status fast path."""
import json
import unittest.mock as mock

import pytest

from sky.server import constants as server_constants
from sky.server import versions
from sky.server.requests import payloads
from sky.server.requests import requests
from sky.server.requests.requests import COL_CLUSTER_NAME
from sky.server.requests.requests import COL_FINISHED_AT
from sky.server.requests.requests import COL_SHOULD_RETRY
from sky.server.requests.requests import COL_STATUS_MSG
from sky.server.requests.requests import RequestStatus
from sky.server.requests.requests import ScheduleType
from sky.server.requests.storage import RequestBackend


def dummy():
    return None


@pytest.fixture()
def isolated_database(tmp_path):
    """Create an isolated requests DB for each test."""
    temp_db_path = tmp_path / 'requests.db'
    temp_log_path = tmp_path / 'logs'
    temp_log_path.mkdir()
    with mock.patch('sky.server.constants.API_SERVER_REQUEST_DB_PATH',
                    str(temp_db_path)), mock.patch(
                        'sky.server.constants.REQUEST_LOG_PATH_PREFIX',
                        str(temp_log_path)):
        requests._DB = None
        yield
        requests._DB = None


@pytest.fixture()
def new_client_version():
    """Pretend the connected client advertises the new API version (fast path)."""
    versions.set_remote_api_version(
        server_constants.MIN_OMIT_UNREQUESTED_FIELDS_API_VERSION)
    yield
    versions.set_remote_api_version(None)


def _make_request(rid,
                  user_id='cooperc',
                  cluster_name='cooperc-repro-cl',
                  finished_at=2.0,
                  should_retry=False):
    return requests.Request(request_id=rid,
                            name='sky.launch',
                            entrypoint=dummy,
                            request_body=payloads.RequestBody(),
                            status=RequestStatus.SUCCEEDED,
                            created_at=1.0,
                            user_id=user_id,
                            cluster_name=cluster_name,
                            finished_at=finished_at,
                            should_retry=should_retry)


def test_request_payload_defaults_match_legacy_placeholders():
    """The 6 newly-defaulted fields default to the legacy wire placeholders so
    a client reconstructing an omitted field sees the same value as before."""
    # pydantic v2 model_fields is a Mapping; pylint's stubs type it as
    # non-subscriptable, so disable the false positive for this function.
    # pylint: disable=unsubscriptable-object
    mf = payloads.RequestPayload.model_fields
    assert mf['entrypoint'].default == ''
    assert mf['request_body'].default == 'null'
    assert mf['return_value'].default == 'null'
    assert mf['error'].default == 'null'
    assert mf['pid'].default is None
    assert mf['schedule_type'].default == ScheduleType.SHORT.value
    # Core identity fields stay required (no default) so a partial construct
    # surfaces immediately rather than silently defaulting the identity. In
    # pydantic v2 a required field's default is the PydanticUndefined sentinel.
    for required in ('request_id', 'name', 'status', 'created_at', 'user_id'):
        assert payloads.RequestPayload.model_fields[required].is_required()


def test_request_payload_dict_from_row_basic():
    """omit_unrequested=True (new client): the dict carries only the projected
    core fields + user_name (the trimmed wire)."""
    fields = requests._FAST_PATH_CORE_FIELDS
    row = ('rid-1', 'sky.launch', 'cooperc', 'SUCCEEDED', 1.0)
    out = requests.request_payload_dict_from_row(row,
                                                 fields,
                                                 {'cooperc': 'Christopher'},
                                                 omit_unrequested=True)
    assert set(out.keys()) == {
        'request_id', 'name', 'user_id', 'status', 'created_at', 'user_name'
    }
    assert out['user_name'] == 'Christopher'
    assert out['status'] == 'SUCCEEDED'


def test_request_payload_dict_from_row_waiting_downgrade_only_for_old_clients():
    """The fast path never downgrades WAITING for new clients (>=58 >= 55)."""
    fields = requests._FAST_PATH_CORE_FIELDS  # 'status' is a core field
    row = ('rid', 'sky.launch', 'cooperc', RequestStatus.WAITING.value, 1.0)
    # New client: WAITING passes through.
    out_new = requests.request_payload_dict_from_row(row,
                                                     fields, {},
                                                     downgrade_waiting=False)
    assert out_new['status'] == RequestStatus.WAITING.value
    # An old-enough client (<55): WAITING is downgraded to RUNNING.
    out_old = requests.request_payload_dict_from_row(row,
                                                     fields, {},
                                                     downgrade_waiting=True)
    assert out_old['status'] == RequestStatus.RUNNING.value


def test_client_reconstructs_trimmed_wire_via_defaults():
    """The client (sdk.api_status) does RequestPayload(**dict); a dict carrying
    only the fast-path fields must reconstruct using the new defaults."""
    d = {
        'request_id': 'rid',
        'name': 'sky.launch',
        'user_id': 'cooperc',
        'status': 'SUCCEEDED',
        'created_at': 1.0,
        'user_name': 'Christopher',
    }
    p = payloads.RequestPayload(**d)
    assert p.request_id == 'rid'
    assert p.user_name == 'Christopher'
    # Omitted fields fall back to the legacy-placeholder defaults.
    assert p.entrypoint == ''
    assert p.request_body == 'null'
    assert p.return_value == 'null'
    assert p.error == 'null'
    assert p.pid is None
    assert p.schedule_type == ScheduleType.SHORT.value


def test_request_payload_dict_from_row_old_client_full_legacy_wire():
    """omit_unrequested=False (old client / no version header): the dict is the
    FULL legacy display wire -- the requested core fields (real) + user_name +
    placeholders for every caller-unrequested field, equivalent to encode_requests
    (16 fields; file_mounts_blob_id omitted, matching legacy) and buildable
    without the per-row decode."""
    fields = requests._FAST_PATH_CORE_FIELDS
    row = ('rid', 'sky.launch', 'cooperc', 'SUCCEEDED', 1.0)
    out = requests.request_payload_dict_from_row(row,
                                                 fields,
                                                 {'cooperc': 'Christopher'},
                                                 omit_unrequested=False)
    # 5 requested + user_name + 10 placeholders = 16 fields (no file_mounts_blob_id)
    assert set(out.keys()) == {
        'request_id',
        'name',
        'user_id',
        'status',
        'created_at',
        'user_name',
        'entrypoint',
        'request_body',
        'return_value',
        'error',
        'pid',
        'schedule_type',
        COL_CLUSTER_NAME,
        COL_STATUS_MSG,
        COL_SHOULD_RETRY,
        COL_FINISHED_AT,
    }
    # The unrequested-field placeholders are the legacy wire values.
    assert out['entrypoint'] == ''
    assert out['request_body'] == 'null'
    assert out['return_value'] == 'null'
    assert out['error'] == 'null'
    assert out['pid'] is None
    assert out['schedule_type'] == ScheduleType.SHORT.value
    assert out[COL_CLUSTER_NAME] is None
    assert out[COL_STATUS_MSG] is None
    assert out[COL_SHOULD_RETRY] is False
    assert out[COL_FINISHED_AT] is None


def test_old_client_reconstructs_full_legacy_wire_without_crash():
    """An old client (whose RequestPayload still requires the 6 now-defaulted
    fields) reconstructs the full 16-field fast-path wire -- the no-default fields
    are present (as legacy placeholders), so no ValidationError. This is why the
    fast path can serve old clients too: the wire is the legacy 16-field wire,
    just built faster."""
    fields = requests._FAST_PATH_CORE_FIELDS
    row = ('rid', 'sky.launch', 'cooperc', 'SUCCEEDED', 1.0)
    out = requests.request_payload_dict_from_row(row,
                                                 fields,
                                                 {'cooperc': 'Christopher'},
                                                 omit_unrequested=False)
    # The no-default fields are all PRESENT in the wire, so any client (old or
    # new) reconstructs RequestPayload(**dict) without a missing-field crash.
    p = payloads.RequestPayload(**out)
    assert p.request_id == 'rid'
    assert p.entrypoint == ''
    assert p.request_body == 'null'
    assert p.pid is None
    assert p.schedule_type == ScheduleType.SHORT.value
    assert p.user_name == 'Christopher'


def test_abc_default_is_concrete_and_not_abstract():
    """The ABC ships a concrete (non-abstract) query_request_payloads_async so a
    backend that does not override it (e.g. an unshipped HA Postgres backend) is
    not forced to implement it in lockstep with the OSS change."""
    assert hasattr(RequestBackend, 'query_request_payloads_async')
    assert ('query_request_payloads_async'
            not in getattr(RequestBackend, '__abstractmethods__', set()))


@pytest.mark.asyncio
async def test_sqlite_fast_override_returns_orjson_bytes_and_trims(
        isolated_database, new_client_version):
    """The Sqlite fast override returns raw orjson bytes whose objects carry
    only the core + user_name fields (the wire is trimmed, not all 17)."""
    for i in range(3):
        await requests.create_if_not_exists_async(_make_request(f'rid-{i}'))
    req_filter = requests.RequestTaskFilter(
        fields=requests._FAST_PATH_CORE_FIELDS,
        sort=True,
    )
    raw = await requests.SqliteRequestBackend().query_request_payloads_async(
        req_filter)
    assert isinstance(raw, bytes)
    data = json.loads(raw)
    assert len(data) == 3
    assert set(data[0].keys()) == {
        'request_id', 'name', 'user_id', 'status', 'created_at', 'user_name'
    }


@pytest.mark.asyncio
async def test_fast_path_equivalent_to_legacy_display_values(
        isolated_database, new_client_version):
    """For a fields-restricted listing, the fast path's reconstructed payloads
    carry the same display values as the legacy encode_requests path -- it just
    omits the caller-unrequested fields, which default to the same placeholders
    the legacy path emitted for a projected query."""
    for i in range(5):
        await requests.create_if_not_exists_async(_make_request(f'rid-{i}'))
    req_filter = requests.RequestTaskFilter(
        fields=requests._FAST_PATH_CORE_FIELDS,
        sort=True,
    )
    # Fast path
    fast = json.loads(
        await requests.SqliteRequestBackend().query_request_payloads_async(
            req_filter))
    # Legacy path (decode + encode)
    decoded = await requests.get_request_tasks_async(req_filter)
    legacy = requests.encode_requests(decoded)
    fast_by_id = {p['request_id']: payloads.RequestPayload(**p) for p in fast}
    legacy_by_id = {p.request_id: p for p in legacy}
    assert set(fast_by_id) == set(legacy_by_id)
    for rid, fp in fast_by_id.items():
        lp = legacy_by_id[rid]
        # The five requested display fields are identical.
        assert fp.request_id == lp.request_id
        assert fp.name == lp.name
        assert fp.user_id == lp.user_id
        assert fp.status == lp.status
        assert fp.created_at == lp.created_at
        # The omitted fields default to the legacy placeholders.
        assert fp.entrypoint == '' and lp.entrypoint == ''
        assert fp.request_body == 'null' and lp.request_body == 'null'
        assert fp.schedule_type == ScheduleType.SHORT.value
        assert lp.schedule_type == ScheduleType.SHORT.value


@pytest.mark.asyncio
async def test_fast_path_equivalent_with_extra_eligible_fields(
        isolated_database, new_client_version):
    """A caller requesting eligible extra fields (cluster_name, should_retry,
    finished_at, status_msg) gets the same client-side values from the fast path
    as from legacy encode_requests; should_retry is coerced to a Python bool on
    the wire (SQLite stores it as 0/1) to match the legacy wire type."""
    for i in range(4):
        await requests.create_if_not_exists_async(
            _make_request(f'rid-{i}', should_retry=(i % 2 == 0)))
    extra = ['cluster_name', 'should_retry', 'finished_at', 'status_msg']
    fields = list(requests._FAST_PATH_CORE_FIELDS) + extra
    req_filter = requests.RequestTaskFilter(fields=fields, sort=True)
    fast = json.loads(
        await requests.SqliteRequestBackend().query_request_payloads_async(
            req_filter))
    decoded = await requests.get_request_tasks_async(req_filter)
    legacy = requests.encode_requests(decoded)
    fast_by_id = {p['request_id']: payloads.RequestPayload(**p) for p in fast}
    legacy_by_id = {p.request_id: p for p in legacy}
    assert set(fast_by_id) == set(legacy_by_id)
    for rid, fp in fast_by_id.items():
        lp = legacy_by_id[rid]
        # Every requested field (core + extras) reconstructs to the same value.
        for field_name in fields + ['user_name']:
            assert getattr(fp,
                           field_name) == getattr(lp,
                                                  field_name), (rid, field_name)
        # should_retry is a Python bool on the wire (legacy parity; SQLite 0/1
        # is coerced), not a JSON number.
        assert isinstance(fp.should_retry, bool)
