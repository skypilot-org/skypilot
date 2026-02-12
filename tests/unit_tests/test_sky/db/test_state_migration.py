"""Tests for sky.utils.db.state_migration."""
# pylint: disable=protected-access,invalid-name
import datetime
import json
import os
import tarfile
import tempfile

import pytest
import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.ext import declarative

from sky.utils.db import state_migration


# ---------------------------------------------------------------------------
# Test helpers — in-memory SQLite tables
# ---------------------------------------------------------------------------
def _make_engine():
    """Create an in-memory SQLite engine."""
    return sqlalchemy.create_engine('sqlite://')


def _make_test_tables():
    """Build a small set of tables for round-trip testing."""
    Base = declarative.declarative_base()

    test_table = sqlalchemy.Table(
        'test_rows',
        Base.metadata,
        sqlalchemy.Column('id', sqlalchemy.Integer, primary_key=True),
        sqlalchemy.Column('name', sqlalchemy.Text),
        sqlalchemy.Column('data', sqlalchemy.LargeBinary),
        sqlalchemy.Column('score', sqlalchemy.Float),
        sqlalchemy.Column('active', sqlalchemy.Boolean),
        sqlalchemy.Column('meta', sqlalchemy.JSON),
        sqlalchemy.Column('created_at', sqlalchemy.DateTime(timezone=True)),
    )

    simple_table = sqlalchemy.Table(
        'simple',
        Base.metadata,
        sqlalchemy.Column('key', sqlalchemy.Text, primary_key=True),
        sqlalchemy.Column('value', sqlalchemy.Text),
    )
    return Base, test_table, simple_table


# ---------------------------------------------------------------------------
# Serialization / deserialization round-trip
# ---------------------------------------------------------------------------
class TestSerializeDeserialize:
    """Verify that each supported column type survives a round-trip."""

    def test_text(self):
        col_type = sqlalchemy.Text()
        assert state_migration._serialize_value('hello', col_type) == 'hello'
        assert state_migration._deserialize_value('hello', col_type) == 'hello'

    def test_integer(self):
        col_type = sqlalchemy.Integer()
        assert state_migration._serialize_value(42, col_type) == 42
        assert state_migration._deserialize_value(42, col_type) == 42

    def test_float(self):
        col_type = sqlalchemy.Float()
        assert state_migration._serialize_value(3.14, col_type) == 3.14
        assert state_migration._deserialize_value(3.14, col_type) == 3.14

    def test_large_binary(self):
        col_type = sqlalchemy.LargeBinary()
        raw = b'\x00\x01\x02\xff'
        serialized = state_migration._serialize_value(raw, col_type)
        assert isinstance(serialized, str)
        assert serialized.startswith(state_migration._BASE64_PREFIX)
        deserialized = state_migration._deserialize_value(serialized, col_type)
        assert deserialized == raw

    def test_json(self):
        col_type = sqlalchemy.JSON()
        obj = {'key': [1, 2, 3], 'nested': {'a': True}}
        assert state_migration._serialize_value(obj, col_type) is obj
        assert state_migration._deserialize_value(obj, col_type) is obj

    def test_datetime(self):
        col_type = sqlalchemy.DateTime(timezone=True)
        dt = datetime.datetime(2025,
                               1,
                               15,
                               10,
                               30,
                               0,
                               tzinfo=datetime.timezone.utc)
        serialized = state_migration._serialize_value(dt, col_type)
        assert isinstance(serialized, str)
        deserialized = state_migration._deserialize_value(serialized, col_type)
        assert deserialized == dt

    def test_boolean(self):
        col_type = sqlalchemy.Boolean()
        assert state_migration._serialize_value(True, col_type) is True
        assert state_migration._serialize_value(False, col_type) is False
        assert state_migration._deserialize_value(True, col_type) is True
        assert state_migration._deserialize_value(1, col_type) is True

    def test_none_values(self):
        for col_type in (sqlalchemy.Text(), sqlalchemy.Integer(),
                         sqlalchemy.LargeBinary(), sqlalchemy.JSON(),
                         sqlalchemy.DateTime(), sqlalchemy.Boolean()):
            assert state_migration._serialize_value(None, col_type) is None
            assert state_migration._deserialize_value(None, col_type) is None


# ---------------------------------------------------------------------------
# Row-level round-trip through an in-memory SQLite DB
# ---------------------------------------------------------------------------
class TestRowRoundTrip:
    """Insert rows, serialize them, then deserialize and compare."""

    def test_round_trip_all_types(self):
        """Insert a row with every type, export, deserialize, verify."""
        Base, test_table, _ = _make_test_tables()
        engine = _make_engine()
        Base.metadata.create_all(engine)

        now = datetime.datetime(2025,
                                6,
                                1,
                                12,
                                0,
                                0,
                                tzinfo=datetime.timezone.utc)
        original = {
            'id': 1,
            'name': 'test-row',
            'data': b'\xde\xad\xbe\xef',
            'score': 99.5,
            'active': True,
            'meta': {
                'foo': 'bar'
            },
            'created_at': now,
        }

        with orm.Session(engine) as session:
            session.execute(test_table.insert().values(**original))
            session.commit()

        with orm.Session(engine) as session:
            rows = session.execute(sqlalchemy.select(test_table)).fetchall()
            assert len(rows) == 1

            serialized = state_migration._serialize_row(rows[0], test_table)

        # Serialized binary should be base64.
        assert serialized['data'].startswith(state_migration._BASE64_PREFIX)
        # Serialized datetime should be a string.
        assert isinstance(serialized['created_at'], str)
        # JSON should remain native.
        assert serialized['meta'] == {'foo': 'bar'}

        deserialized = state_migration._deserialize_row(serialized, test_table)
        assert deserialized['id'] == 1
        assert deserialized['name'] == 'test-row'
        assert deserialized['data'] == b'\xde\xad\xbe\xef'
        assert deserialized['score'] == 99.5
        assert deserialized['active'] is True
        assert deserialized['meta'] == {'foo': 'bar'}
        # SQLite strips timezone info from DateTime columns, so we only
        # compare the naive datetime portion.
        assert deserialized['created_at'].replace(tzinfo=None) == \
            now.replace(tzinfo=None)

    def test_round_trip_with_nulls(self):
        Base, test_table, _ = _make_test_tables()
        engine = _make_engine()
        Base.metadata.create_all(engine)

        with orm.Session(engine) as session:
            session.execute(test_table.insert().values(id=1,
                                                       name=None,
                                                       data=None,
                                                       score=None,
                                                       active=None,
                                                       meta=None,
                                                       created_at=None))
            session.commit()

        with orm.Session(engine) as session:
            rows = session.execute(sqlalchemy.select(test_table)).fetchall()
            serialized = state_migration._serialize_row(rows[0], test_table)

        deserialized = state_migration._deserialize_row(serialized, test_table)
        assert deserialized['id'] == 1
        for col_name in ('name', 'data', 'score', 'active', 'meta',
                         'created_at'):
            assert deserialized[col_name] is None

    def test_insert_deserialized_rows(self):
        """Verify that deserialized rows can be re-inserted into a fresh DB."""
        Base, test_table, _ = _make_test_tables()
        engine_src = _make_engine()
        engine_dst = _make_engine()
        Base.metadata.create_all(engine_src)
        Base.metadata.create_all(engine_dst)

        now = datetime.datetime(2025,
                                6,
                                1,
                                12,
                                0,
                                0,
                                tzinfo=datetime.timezone.utc)
        with orm.Session(engine_src) as session:
            session.execute(test_table.insert().values(id=1,
                                                       name='row1',
                                                       data=b'abc',
                                                       score=1.0,
                                                       active=True,
                                                       meta={'x': 1},
                                                       created_at=now))
            session.execute(test_table.insert().values(id=2,
                                                       name='row2',
                                                       data=b'def',
                                                       score=2.0,
                                                       active=False,
                                                       meta=None,
                                                       created_at=None))
            session.commit()

        # Export.
        with orm.Session(engine_src) as session:
            rows = session.execute(sqlalchemy.select(test_table)).fetchall()
            serialized = [
                state_migration._serialize_row(r, test_table) for r in rows
            ]

        # Import into destination.
        deserialized = [
            state_migration._deserialize_row(s, test_table) for s in serialized
        ]
        with orm.Session(engine_dst) as session:
            session.execute(test_table.insert(), deserialized)
            session.commit()

        # Verify.
        with orm.Session(engine_dst) as session:
            result = session.execute(sqlalchemy.select(test_table)).fetchall()
            assert len(result) == 2
            assert result[0].name == 'row1'
            assert result[1].name == 'row2'


# ---------------------------------------------------------------------------
# Column info helper
# ---------------------------------------------------------------------------
class TestColumnInfo:
    """Tests for _get_column_info."""

    def test_basic_columns(self):
        _, test_table, _ = _make_test_tables()
        info = state_migration._get_column_info(test_table)
        names = [c['name'] for c in info]
        assert 'id' in names
        assert 'name' in names
        assert 'data' in names
        id_info = next(c for c in info if c['name'] == 'id')
        assert id_info.get('primary_key') is True


# ---------------------------------------------------------------------------
# Table import ordering
# ---------------------------------------------------------------------------
class TestTableImportOrder:
    """Tests for _get_table_import_order."""

    def test_spot_jobs_order(self):
        tables = {
            'spot': {},
            'job_info': {},
            'ha_recovery_script': {},
            'job_events': {},
        }
        order = state_migration._get_table_import_order('spot_jobs_db', tables)
        assert order.index('job_info') < order.index('spot')
        assert order.index('spot') < order.index('job_events')

    def test_serve_order(self):
        tables = {
            'replicas': {},
            'services': {},
            'version_specs': {},
            'serve_ha_recovery_script': {},
        }
        order = state_migration._get_table_import_order('serve_db', tables)
        assert order.index('services') < order.index('replicas')

    def test_unknown_db_passthrough(self):
        tables = {'alpha': {}, 'beta': {}}
        order = state_migration._get_table_import_order('other_db', tables)
        assert set(order) == {'alpha', 'beta'}


# ---------------------------------------------------------------------------
# Tarball safety
# ---------------------------------------------------------------------------
class TestSafeExtract:
    """Tests for _safe_extract tarball safety."""

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = os.path.join(tmpdir, 'evil.tar.gz')
            with tarfile.open(tar_path, 'w:gz') as tar:
                # Create a member that escapes the destination.
                info = tarfile.TarInfo(name='../../etc/passwd')
                info.size = 0
                import io  # pylint: disable=import-outside-toplevel
                tar.addfile(info, io.BytesIO(b''))

            with tarfile.open(tar_path, 'r:gz') as tar:
                with pytest.raises(ValueError, match='Path traversal'):
                    state_migration._safe_extract(tar, tmpdir)

    def test_accepts_normal_archive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a normal tarball.
            tar_path = os.path.join(tmpdir, 'good.tar.gz')
            src_dir = os.path.join(tmpdir, 'src')
            os.makedirs(os.path.join(src_dir, 'subdir'))
            with open(os.path.join(src_dir, 'subdir', 'file.txt'),
                      'w',
                      encoding='utf-8') as f:
                f.write('hello')
            with tarfile.open(tar_path, 'w:gz') as tar:
                tar.add(src_dir, arcname='src')

            dest = os.path.join(tmpdir, 'dest')
            os.makedirs(dest)
            with tarfile.open(tar_path, 'r:gz') as tar:
                state_migration._safe_extract(tar, dest)
            assert os.path.exists(
                os.path.join(dest, 'src', 'subdir', 'file.txt'))


# ---------------------------------------------------------------------------
# Full export → import round-trip (SQLite only, in-memory not possible
# for full flow because get_engine uses file-based paths).
# ---------------------------------------------------------------------------
class TestFullRoundTrip:
    """Full round-trip: create tables → populate → export → import."""

    def test_sqlite_round_trip(self, tmp_path):
        """Test export → import using file-based SQLite databases."""
        # Create source and target directories to simulate ~/.sky.
        src_sky = tmp_path / 'src_sky'
        dst_sky = tmp_path / 'dst_sky'
        src_sky.mkdir()
        dst_sky.mkdir()

        # Create a simple table and populate it.
        Base, _, simple_table = _make_test_tables()
        src_db = str(src_sky / 'test.db')
        dst_db = str(dst_sky / 'test.db')

        src_engine = sqlalchemy.create_engine(f'sqlite:///{src_db}')
        dst_engine = sqlalchemy.create_engine(f'sqlite:///{dst_db}')

        Base.metadata.create_all(src_engine)
        Base.metadata.create_all(dst_engine)

        # Insert test data.
        with orm.Session(src_engine) as session:
            session.execute(simple_table.insert().values(key='k1', value='v1'))
            session.execute(simple_table.insert().values(key='k2', value='v2'))
            session.commit()

        # Manually do the serialize → deserialize loop.
        with orm.Session(src_engine) as session:
            rows = session.execute(sqlalchemy.select(simple_table)).fetchall()
            serialized = [
                state_migration._serialize_row(r, simple_table) for r in rows
            ]

        deserialized = [
            state_migration._deserialize_row(s, simple_table)
            for s in serialized
        ]

        with orm.Session(dst_engine) as session:
            session.execute(simple_table.insert(), deserialized)
            session.commit()

        # Verify.
        with orm.Session(dst_engine) as session:
            result = session.execute(sqlalchemy.select(simple_table)).fetchall()
            assert len(result) == 2
            keys = {r.key for r in result}
            assert keys == {'k1', 'k2'}

    def test_json_export_format(self):
        """Verify the JSON structure matches the documented format."""
        Base, test_table, _ = _make_test_tables()
        engine = _make_engine()
        Base.metadata.create_all(engine)

        now = datetime.datetime(2025,
                                6,
                                1,
                                0,
                                0,
                                0,
                                tzinfo=datetime.timezone.utc)
        with orm.Session(engine) as session:
            session.execute(test_table.insert().values(id=1,
                                                       name='hello',
                                                       data=b'\x01\x02',
                                                       score=1.5,
                                                       active=True,
                                                       meta={'a': 1},
                                                       created_at=now))
            session.commit()

        with orm.Session(engine) as session:
            rows = session.execute(sqlalchemy.select(test_table)).fetchall()
            serialized = state_migration._serialize_row(rows[0], test_table)

        # Verify it's JSON-serializable.
        json_str = json.dumps(serialized)
        parsed = json.loads(json_str)

        assert parsed['id'] == 1
        assert parsed['name'] == 'hello'
        assert parsed['data'].startswith('b64:')
        assert parsed['score'] == 1.5
        assert parsed['active'] is True
        assert parsed['meta'] == {'a': 1}
        assert isinstance(parsed['created_at'], str)
