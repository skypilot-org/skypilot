"""Schema-shape regression tests for sky.jobs.state."""

import sqlalchemy

from sky.jobs.state import spot_table


def test_spot_has_below_min_since_and_all_ready_at():
    """The spot table includes below_min_since and all_ready_at."""
    cols = {c.name for c in spot_table.columns}
    assert 'below_min_since' in cols
    assert 'all_ready_at' in cols


def test_spot_elastic_columns_are_nullable_float():
    """Both new columns are nullable Float (matches last_recovered_at shape)."""
    for name in ('below_min_since', 'all_ready_at'):
        col = spot_table.c[name]
        assert col.nullable is True, f'{name} should be nullable'
        assert isinstance(col.type, sqlalchemy.Float), (
            f'{name} type {col.type!r} should be Float')
