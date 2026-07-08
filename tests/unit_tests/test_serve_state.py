"""Tests for serve_state.

Focused on the new controller_ip column + atomic update introduced for HA
leader-aware routing.
"""
# Pytest fixture name collides with pylint's "private name" rule (leading
# underscore is the standard convention for fixtures injected for side
# effects). Disable for the file.
# pylint: disable=invalid-name,protected-access
import pytest
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy import orm

from sky.serve import serve_state


def _read_row(engine, name):
    """Read raw services row directly (bypassing get_service_from_name which
    does an INNER JOIN with version_specs and would skip rows without a
    version registered)."""
    with orm.Session(engine) as session:
        result = session.execute(
            sqlalchemy.select(serve_state.services_table).where(
                serve_state.services_table.c.name == name)).fetchone()
    return None if result is None else dict(result._mapping)  # pylint: disable=protected-access


@pytest.fixture
def _mock_serve_db(tmp_path, monkeypatch):
    """Point serve_state at a fresh sqlite DB for the duration of one test."""
    db_path = tmp_path / 'serve_state_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')

    monkeypatch.setattr(serve_state._db_manager, '_engine', engine)
    # `metadata.create_all` only creates tables that don't already exist; this
    # is enough since we're starting from a brand-new DB and the alembic
    # upgrade step in `create_table` would otherwise need a full env.
    serve_state.Base.metadata.create_all(engine)
    yield engine


def _add_minimal_service(name: str, controller_ip=None):
    """Add a service row with all-required-args defaults so individual tests
    only need to specify what they care about."""
    return serve_state.add_service(
        name=name,
        controller_job_id=1,
        policy='policy',
        requested_resources_str='1x[CPU:1+]',
        load_balancing_policy='round_robin',
        status=serve_state.ServiceStatus.CONTROLLER_INIT,
        tls_encrypted=False,
        pool=False,
        controller_pid=12345,
        entrypoint='entry',
        controller_ip=controller_ip,
    )


class TestAddServiceWritesControllerIp:
    """`add_service` should persist the new controller_ip column when caller
    provides POD_IP. Older callers that don't pass it must still work
    (column defaults to NULL)."""

    def test_with_controller_ip(self, _mock_serve_db):
        success = _add_minimal_service('svc1', controller_ip='10.0.0.7')
        assert success is True
        record = _read_row(_mock_serve_db, 'svc1')
        assert record is not None
        assert record['controller_ip'] == '10.0.0.7'

    def test_without_controller_ip(self, _mock_serve_db):
        # No controller_ip arg → column stays NULL (used by single-pod /
        # non-K8s deployments where the routing layer falls back to localhost).
        success = _add_minimal_service('svc2')
        assert success is True
        record = _read_row(_mock_serve_db, 'svc2')
        assert record is not None
        assert record['controller_ip'] is None

    def test_returns_false_on_duplicate(self, _mock_serve_db):
        assert _add_minimal_service('svc3', controller_ip='10.0.0.7') is True
        # Adding the same service again must not corrupt — uniqueness violation
        # is converted to False so up() can short-circuit.
        assert _add_minimal_service('svc3', controller_ip='10.0.0.8') is False
        # And the row is unchanged.
        record = _read_row(_mock_serve_db, 'svc3')
        assert record['controller_ip'] == '10.0.0.7'


class TestGetServiceFromNameReturnsControllerIp:

    def _add_with_version(self, service_name, controller_ip):
        # Reading via get_service_from_name requires a version_specs row
        # (it's an INNER JOIN). Use the lightweight `add_version` helper,
        # which inserts with spec=pickle.dumps(None) and yaml_content=NULL —
        # enough for the JOIN to fire, no SkyServiceSpec wrangling needed.
        _add_minimal_service(service_name, controller_ip=controller_ip)
        serve_state.add_version(service_name)

    def test_round_trips_controller_ip(self, _mock_serve_db):
        self._add_with_version('svc-rt', controller_ip='10.4.10.8')
        record = serve_state.get_service_from_name('svc-rt')
        assert record is not None
        # The whole point: the dict KEY must exist with the persisted value.
        assert 'controller_ip' in record, (
            'controller_ip key missing from get_service_from_name() record — '
            'callers will silently fall back to localhost routing')
        assert record['controller_ip'] == '10.4.10.8'

    def test_round_trips_none_controller_ip(self, _mock_serve_db):
        # Even when the row was written without controller_ip (single-pod
        # mode), the dict must contain the key with value None — otherwise
        # `record.get('controller_ip')` and `record['controller_ip']` give
        # inconsistent answers.
        self._add_with_version('svc-rt-none', controller_ip=None)
        record = serve_state.get_service_from_name('svc-rt-none')
        assert record is not None
        assert 'controller_ip' in record
        assert record['controller_ip'] is None

    def test_record_includes_all_persisted_columns(self, _mock_serve_db):
        """Belt and braces: snapshot the keys we expect from the read path.
        If a future PR adds a column to services_table but forgets to update
        `_get_service_from_row`, this test fails loudly."""
        self._add_with_version('svc-rt-keys', controller_ip='10.0.0.1')
        record = serve_state.get_service_from_name('svc-rt-keys')
        assert record is not None
        for key in (
                'name',
                'status',
                'controller_pid',
                'controller_ip',
                'controller_port',
                'pool',
        ):
            assert key in record, f'missing key: {key}'


class TestUpdateServiceControllerPidIpAndPort:
    """The atomic update is the core of the HA-recovery DB flip — it must
    write pid, ip, AND port in a single transaction so clients never
    observe a half-flipped row (e.g. new pid + old ip + stale port that
    points at a different service's listener on the new pod).

    Recovery picks port locally (find_free_port on the recovery pod);
    that new port has to land in DB together with pid/ip, otherwise
    a `_get_controller_url` consumer reading DB between writes could
    route to the new pod with the old port and hit the wrong listener.
    """

    def test_updates_all_three_fields(self, _mock_serve_db):
        _add_minimal_service('svc', controller_ip='10.0.0.7')
        serve_state.set_service_controller_port('svc', 20001)

        serve_state.update_service_controller_pid_ip_and_port(
            'svc',
            controller_pid=99999,
            controller_ip='10.0.0.8',
            controller_port=20007)

        record = _read_row(_mock_serve_db, 'svc')
        assert record['controller_pid'] == 99999
        assert record['controller_ip'] == '10.0.0.8'
        assert record['controller_port'] == 20007

    def test_can_clear_controller_ip(self, _mock_serve_db):
        # If we ever need to write None (e.g. controller is on a non-K8s pod
        # in a hybrid deploy), the column must accept NULL. Port is still
        # required (it's an int column, no NULL).
        _add_minimal_service('svc', controller_ip='10.0.0.7')
        serve_state.update_service_controller_pid_ip_and_port(
            'svc',
            controller_pid=99999,
            controller_ip=None,
            controller_port=20007)
        record = _read_row(_mock_serve_db, 'svc')
        assert record['controller_pid'] == 99999
        assert record['controller_ip'] is None
        assert record['controller_port'] == 20007

    def test_no_op_when_service_missing(self, _mock_serve_db):
        # Should not raise if the row was deleted between read and write
        # (e.g. a `down` raced our recovery).
        serve_state.update_service_controller_pid_ip_and_port(
            'never-existed',
            controller_pid=1,
            controller_ip='10.0.0.7',
            controller_port=20001)
        assert _read_row(_mock_serve_db, 'never-existed') is None

    def test_does_not_touch_other_fields(self, _mock_serve_db):
        # The atomic update must only touch the three specified columns —
        # don't want to clobber e.g. status, load_balancer_port from a
        # concurrent writer.
        _add_minimal_service('svc', controller_ip='10.0.0.7')
        serve_state.set_service_controller_port('svc', 20001)
        serve_state.set_service_load_balancer_port('svc', 30000)

        serve_state.update_service_controller_pid_ip_and_port(
            'svc',
            controller_pid=99999,
            controller_ip='10.0.0.8',
            controller_port=20007)

        record = _read_row(_mock_serve_db, 'svc')
        assert record['controller_pid'] == 99999
        assert record['controller_ip'] == '10.0.0.8'
        assert record['controller_port'] == 20007
        assert record['load_balancer_port'] == 30000  # untouched


class TestSetServiceControllerIp:
    """Standalone IP setter is rarely called (the atomic version is preferred
    for recovery), but kept for symmetry with set_service_controller_port."""

    def test_basic(self, _mock_serve_db):
        _add_minimal_service('svc', controller_ip=None)
        serve_state.set_service_controller_ip('svc', '10.0.0.9')
        record = _read_row(_mock_serve_db, 'svc')
        assert record['controller_ip'] == '10.0.0.9'
        # pid unchanged
        assert record['controller_pid'] == 12345


class TestRemoveServiceCompletely:
    """`remove_service_completely` deletes services / version_specs /
    serve_ha_recovery_script in one transaction. Sequential deletes had
    a real-world failure mode where the last call
    was the one most likely to be skipped
    when the subprocess died mid-cleanup, leaving the row orphaned across
    many test runs while the other tables stayed clean. This guarantees
    all-or-nothing teardown for service metadata.
    """

    def _populate(self, engine, name):
        # Seed all three service-metadata tables plus a replica row so we
        # can assert metadata is removed atomically while the replica row
        # survives.
        _add_minimal_service(name, controller_ip='10.0.0.1')
        serve_state.add_version(name)
        serve_state.set_ha_recovery_script(name, 'dummy script')
        # replicas: a minimal pickled ReplicaInfo proxy is hard to
        # construct here, so just write a row directly to the table.
        with orm.Session(engine) as session:
            session.execute(serve_state.replicas_table.insert().values(
                service_name=name, replica_id=1, replica_info=b'fake-pickle'))
            session.commit()

    def test_removes_service_metadata_tables(self, _mock_serve_db):
        self._populate(_mock_serve_db, 'svc-rsc')
        # Sanity: rows are present.
        with orm.Session(_mock_serve_db) as session:
            assert session.execute(
                sqlalchemy.select(serve_state.services_table.c.name).where(
                    serve_state.services_table.c.name ==
                    'svc-rsc')).first() is not None
            assert session.execute(
                sqlalchemy.select(
                    serve_state.serve_ha_recovery_script_table.c.service_name).
                where(serve_state.serve_ha_recovery_script_table.c.service_name
                      == 'svc-rsc')).first() is not None
            assert session.execute(
                sqlalchemy.select(
                    serve_state.replicas_table.c.service_name).where(
                        serve_state.replicas_table.c.service_name ==
                        'svc-rsc')).first() is not None
            assert session.execute(
                sqlalchemy.select(
                    serve_state.version_specs_table.c.service_name).where(
                        serve_state.version_specs_table.c.service_name ==
                        'svc-rsc')).first() is not None

        serve_state.remove_service_completely('svc-rsc')

        # The three metadata tables must be gone.
        with orm.Session(_mock_serve_db) as session:
            assert session.execute(
                sqlalchemy.select(serve_state.services_table.c.name).where(
                    serve_state.services_table.c.name ==
                    'svc-rsc')).first() is None
            assert session.execute(
                sqlalchemy.select(
                    serve_state.serve_ha_recovery_script_table.c.service_name).
                where(serve_state.serve_ha_recovery_script_table.c.service_name
                      == 'svc-rsc')).first() is None
            assert session.execute(
                sqlalchemy.select(
                    serve_state.version_specs_table.c.service_name).where(
                        serve_state.version_specs_table.c.service_name ==
                        'svc-rsc')).first() is None
            # The replicas row must SURVIVE — replicas are the caller's
            # responsibility, not this function's. Deleting them here
            # would fold over the per-replica leak-detection and
            # failure-marking logic in the two real callers.
            assert session.execute(
                sqlalchemy.select(
                    serve_state.replicas_table.c.service_name).where(
                        serve_state.replicas_table.c.service_name ==
                        'svc-rsc')).first() is not None

    def test_does_not_touch_other_services(self, _mock_serve_db):
        """Make sure deletion is scoped to the named service."""
        self._populate(_mock_serve_db, 'svc-keep')
        self._populate(_mock_serve_db, 'svc-drop')

        serve_state.remove_service_completely('svc-drop')

        # svc-keep's rows must all survive.
        with orm.Session(_mock_serve_db) as session:
            for tbl_name, tbl, col_name in [
                ('services', serve_state.services_table, 'name'),
                ('version_specs', serve_state.version_specs_table,
                 'service_name'),
                ('serve_ha_recovery_script',
                 serve_state.serve_ha_recovery_script_table, 'service_name'),
                ('replicas', serve_state.replicas_table, 'service_name'),
            ]:
                col = getattr(tbl.c, col_name)
                assert session.execute(
                    sqlalchemy.select(col).where(
                        col == 'svc-keep')).first() is not None, (
                            f'svc-keep was wrongly removed from {tbl_name}')

    def test_no_op_when_nothing_to_delete(self, _mock_serve_db):
        # Should be a silent no-op when no rows exist.
        serve_state.remove_service_completely('never-existed')
        # No assertion needed beyond "didn't raise".
