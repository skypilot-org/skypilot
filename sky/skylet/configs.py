"""skylet configs"""
import json
import os
import pathlib
import psutil
import sqlite3
from typing import Optional

_DB_PATH = os.path.expanduser('~/.sky/skylet_config.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)

_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY, 
        value TEXT)""")

_CONN.commit()

_AUTOSTOP_CONFIG_KEY = 'autostop_config'


class AutostopConfig:

    def __init__(self, autostop_idle_minutes: int, boot_time: int):
        self.autostop_idle_minutes = autostop_idle_minutes
        self.boot_time = boot_time


def get_autostop_config() -> Optional[AutostopConfig]:
    rows = _CURSOR.execute('SELECT value FROM config WHERE key = ?',
                           (_AUTOSTOP_CONFIG_KEY,))
    for (value,) in rows:
        if value is None:
            return AutostopConfig(-1, -1)
        return json.loads(value)


def set_idle_minutes(idle_minutes: int) -> None:
    boot_time = psutil.boot_time()
    autostop_config = AutostopConfig(idle_minutes, boot_time)
    _CURSOR.execute(
        """\
        INSERT OR REPLACE INTO config (?, ?)
        """, (_AUTOSTOP_CONFIG_KEY, json.dumps(autostop_config)))
