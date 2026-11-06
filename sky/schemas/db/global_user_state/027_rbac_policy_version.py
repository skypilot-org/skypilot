"""Add rbac_policy_version, bumped by triggers on every casbin_rule write.

Revision ID: 027
Revises: 026
Create Date: 2026-11-05

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '027'
down_revision: Union[str, Sequence[str], None] = '026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Names are frozen here; sky/users/permission.py reads the same ones.
_PG_FUNCTION = 'rbac_policy_version_bump'
_PG_TRIGGER = 'casbin_rule_bump_policy_version'
_SQLITE_TRIGGERS = {
    'casbin_rule_bump_policy_version_insert': 'INSERT',
    'casbin_rule_bump_policy_version_update': 'UPDATE',
    'casbin_rule_bump_policy_version_delete': 'DELETE',
}
# Makes this DDL race-safe for concurrent migrators on one Postgres (the
# migration file lock is host-local). A losing migrator can still fail later on
# alembic's version-table UPDATE, as with every migration.
_PG_ADVISORY_LOCK_KEY = 0x72626163  # 'rbac'

_BUMP = 'UPDATE rbac_policy_version SET version = version + 1 WHERE id = 1'


def _tables() -> Sequence[sa.Table]:
    metadata = sa.MetaData()
    # casbin_rule is normally created lazily by the casbin adapter; the trigger
    # needs it now. Columns match sqlalchemy_adapter.CasbinRule.
    casbin_rule = sa.Table(
        'casbin_rule', metadata, sa.Column('id', sa.Integer, primary_key=True),
        *[
            sa.Column(name, sa.String(255))
            for name in ('ptype', 'v0', 'v1', 'v2', 'v3', 'v4', 'v5')
        ])
    version = sa.Table('rbac_policy_version', metadata,
                       sa.Column('id', sa.Integer, primary_key=True),
                       sa.Column('version', sa.BigInteger, nullable=False))
    return casbin_rule, version


def upgrade_on(connection: sa.engine.Connection) -> None:
    """Idempotent, so concurrent or repeated runs converge."""
    is_postgres = (
        connection.dialect.name == db_utils.SQLAlchemyDialect.POSTGRESQL.value)
    if is_postgres:
        connection.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),
                           {'key': _PG_ADVISORY_LOCK_KEY})
    for table in _tables():
        table.create(connection, checkfirst=True)
    connection.execute(
        sa.text('INSERT INTO rbac_policy_version (id, version) '
                'VALUES (1, 0) ON CONFLICT DO NOTHING'))
    if is_postgres:
        connection.execute(
            sa.text(f'CREATE OR REPLACE FUNCTION {_PG_FUNCTION}() '
                    f'RETURNS trigger AS $$ BEGIN {_BUMP}; RETURN NULL; END; '
                    '$$ LANGUAGE plpgsql'))
        # Not CREATE OR REPLACE TRIGGER, which needs Postgres 14.
        connection.execute(
            sa.text(f'DROP TRIGGER IF EXISTS {_PG_TRIGGER} ON casbin_rule'))
        connection.execute(
            sa.text(f'CREATE TRIGGER {_PG_TRIGGER} '
                    'AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE '
                    'ON casbin_rule FOR EACH STATEMENT '
                    f'EXECUTE PROCEDURE {_PG_FUNCTION}()'))
    else:
        for name, event in _SQLITE_TRIGGERS.items():
            connection.execute(
                sa.text(f'CREATE TRIGGER IF NOT EXISTS {name} '
                        f'AFTER {event} ON casbin_rule FOR EACH ROW '
                        f'BEGIN {_BUMP}; END'))


def upgrade():
    upgrade_on(op.get_bind())


def downgrade():
    """Drop the triggers first, or every casbin_rule write would fail."""
    connection = op.get_bind()
    if connection.dialect.name == db_utils.SQLAlchemyDialect.POSTGRESQL.value:
        connection.execute(
            sa.text(f'DROP TRIGGER IF EXISTS {_PG_TRIGGER} ON casbin_rule'))
        connection.execute(sa.text(f'DROP FUNCTION IF EXISTS {_PG_FUNCTION}()'))
    else:
        for name in _SQLITE_TRIGGERS:
            connection.execute(sa.text(f'DROP TRIGGER IF EXISTS {name}'))
    connection.execute(sa.text('DROP TABLE IF EXISTS rbac_policy_version'))
