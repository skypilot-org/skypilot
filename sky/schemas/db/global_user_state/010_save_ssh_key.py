"""Add ssh keys in filesystem to global user state.

Revision ID: 010
Revises: 009
Create Date: 2025-10-07

"""
import glob
# pylint: disable=invalid-name
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '010'
down_revision: Union[str, Sequence[str], None] = '009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    """Add ssh keys if it was not already added to global user state."""
    connection = op.get_bind()

    match_dirs = glob.glob(os.path.expanduser('~/.sky/clients/*/ssh'))
    file_user_hashes = set()
    for match_dir in match_dirs:
        user_hash = match_dir.split('/')[-2]
        file_user_hashes.add(user_hash)

    # Get all existing ssh keys
    existing_user_hashes = set()
    result = connection.execute(sa.text('SELECT user_hash FROM ssh_key'))
    for row in result:
        existing_user_hashes.add(row[0])

    user_hashes_to_add = file_user_hashes - existing_user_hashes
    for user_hash in user_hashes_to_add:
        match_dir = os.path.join(os.path.expanduser('~/.sky/clients'),
                                 user_hash, 'ssh')
        public_key_path = os.path.join(match_dir, 'sky-key.pub')
        private_key_path = os.path.join(match_dir, 'sky-key')
        try:
            with open(public_key_path, 'r', encoding='utf-8') as f:
                public_key = f.read().strip()
            with open(private_key_path, 'r', encoding='utf-8') as f:
                private_key = f.read().strip()
        except FileNotFoundError:
            # Skip if the key files are not found
            continue
        connection.execute(
            sa.text('INSERT INTO ssh_key '
                    '(user_hash, ssh_public_key, ssh_private_key) '
                    'VALUES (:user_hash, :ssh_public_key, :ssh_private_key) '
                    'ON CONFLICT DO NOTHING'), {
                        'user_hash': user_hash,
                        'ssh_public_key': public_key,
                        'ssh_private_key': private_key
                    })


def downgrade():
    """No-op for backward compatibility."""
    pass
