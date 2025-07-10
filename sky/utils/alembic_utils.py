"""Utilities for working with Alembic database migrations."""

import os

from alembic.config import Config
import sqlalchemy


def get_alembic_config(engine: sqlalchemy.engine.Engine, section: str):
    """Get Alembic configuration for the given section"""
    # Use the alembic.ini file from setup_files (included in wheel)
    alembic_ini_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                    'setup_files', 'alembic.ini')
    alembic_cfg = Config(alembic_ini_path, ini_section=section)

    # Override the database URL to match SkyPilot's current connection
    alembic_cfg.set_main_option('sqlalchemy.url', str(engine.url))

    return alembic_cfg
