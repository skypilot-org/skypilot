"""Add unique index on service_account_tokens.token_hash.

Revision ID: 017
Revises: 016
Create Date: 2026-05-14

The auth middleware looks up service account token rows by their sha256
hash on every authenticated request. Without an index this is a full
table scan; the unique constraint also defends against any future
duplicate-hash insertion bugs.

"""
# pylint: disable=invalid-name
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '017'
down_revision: Union[str, Sequence[str], None] = '016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = 'ix_service_account_tokens_token_hash'
_TABLE_NAME = 'service_account_tokens'


def upgrade():
    """Add unique index on token_hash for fast hash-based authentication.

    Idempotent: skips creation if an index with the same name already
    exists (e.g. created manually out-of-band or by a prior partial run).
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {ix['name'] for ix in inspector.get_indexes(_TABLE_NAME)}
    if _INDEX_NAME in existing:
        return

    with op.get_context().autocommit_block():
        op.create_index(_INDEX_NAME, _TABLE_NAME, ['token_hash'], unique=True)


def downgrade():
    """No-op for backward compatibility."""
    pass
