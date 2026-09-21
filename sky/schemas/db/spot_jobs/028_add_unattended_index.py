"""Add the partial index the stall scan's claimed half needs.

The scan in sky/jobs/stall.py asks, every collector refresh, for tasks the
scheduler claimed that have neither started nor finished and whose claim is
older than a threshold. Nothing covered that predicate, so it was a sequential
scan of every task ever run -- measured at 4.9 ms over 9387 rows on a real
deployment, against 2.3 ms for the indexed half next to it, and the spot
table is never pruned.

Partial, following 027: the predicate holds in-flight claimed work and a row
leaves it the moment it starts or ends, so the index tracks what is
outstanding rather than job history. The predicate is imported from state.py,
where the query's WHERE is built from the same constant -- an index whose
predicate has drifted from its query is silently not used, and nothing fails.

Revision ID: 028
Revises: 027
Create Date: 2026-09-20

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sky.jobs import state

# revision identifiers, used by Alembic.
revision: str = '028'
down_revision: Union[str, Sequence[str], None] = '027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UNATTENDED_INDEX = 'ix_spot_unattended'


def upgrade():
    """Add the claimed-in-flight index."""
    bind = op.get_bind()
    existing = {ix['name'] for ix in sa.inspect(bind).get_indexes('spot')}
    # Dropped first rather than skipped when present, for 027's reason: this
    # revision is unreleased, so a deployment that ran an earlier version of it
    # would otherwise keep that index forever.
    with op.get_context().autocommit_block():
        if _UNATTENDED_INDEX in existing:
            op.drop_index(_UNATTENDED_INDEX, table_name='spot')
        op.create_index(_UNATTENDED_INDEX,
                        'spot', ['submitted_at'],
                        postgresql_where=sa.text(
                            state.CLAIMED_IN_FLIGHT_PREDICATE),
                        sqlite_where=sa.text(state.CLAIMED_IN_FLIGHT_PREDICATE))


def downgrade():
    """No-op for backward compatibility."""
    pass
