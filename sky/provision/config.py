"""Configuration for the provision module."""

import dataclasses
import os
import pathlib
import threading


@dataclasses.dataclass
class _ProvisionConfig(threading.local):
    provision_log: pathlib.Path = pathlib.Path(os.devnull)


config = _ProvisionConfig()
