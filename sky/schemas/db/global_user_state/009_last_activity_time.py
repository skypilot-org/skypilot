"""Add last_activity_time to cluster history.

Revision ID: 009
Revises: 008
Create Date: 2025-09-24

"""
# pylint: disable=invalid-name
from typing import Sequence, Union
import pickle

from alembic import op
import sqlalchemy as sa

from sky.utils.db import db_utils

# revision identifiers, used by Alembic.
revision: str = '009'
down_revision: Union[str, Sequence[str], None] = '008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add last_activity_time column to cluster history."""
    with op.get_context().autocommit_block():
        # Add the column first
        db_utils.add_column_to_table_alembic('cluster_history',
                                             'last_activity_time',
                                             sa.Integer(),
                                             server_default=None)
        
        # Populate the column for existing rows
        _populate_last_activity_time()


def _populate_last_activity_time():
    """Populate last_activity_time for existing rows using usage_intervals logic."""
    connection = op.get_bind()
    
    # Get all existing rows with usage_intervals
    result = connection.execute(
        sa.text("SELECT cluster_hash, usage_intervals FROM cluster_history "
                "WHERE usage_intervals IS NOT NULL")
    )
    
    for row in result:
        cluster_hash = row[0]
        usage_intervals_blob = row[1]
        
        try:
            # Deserialize the usage_intervals
            usage_intervals = pickle.loads(usage_intervals_blob)
            
            if usage_intervals:
                # Apply the same logic as in the filtering code:
                # Get the end time of the last interval (or start time if still running)
                last_interval = usage_intervals[-1]
                last_activity_time = (last_interval[1] if last_interval[1] is not None 
                                    else last_interval[0])
                
                # Update the row with the calculated last_activity_time
                connection.execute(
                    sa.text("UPDATE cluster_history SET last_activity_time = :last_activity_time "
                           "WHERE cluster_hash = :cluster_hash"),
                    {
                        'last_activity_time': last_activity_time,
                        'cluster_hash': cluster_hash
                    }
                )
        except (pickle.PickleError, AttributeError, IndexError):
            # Skip rows with corrupted or invalid usage_intervals
            continue


def downgrade():
    """No-op for backward compatibility."""
    pass
