"""Configuration for the provision module."""

import contextlib
import dataclasses
import logging
import os
import pathlib
import sys
import threading

from sky import sky_logging


@dataclasses.dataclass
class _LoggingConfig(threading.local):
    log_path: pathlib.Path = pathlib.Path(os.devnull)


config = _LoggingConfig()


@contextlib.contextmanager
def setup_provision_logging(log_path: str, file_handler: logging.FileHandler):
    try:
        # Redirect underlying provision logs to file.
        log_abs_path = pathlib.Path(log_path).expanduser().absolute()
        provision_logger = logging.getLogger('sky.provision')
        stream_handler = sky_logging.RichSafeStreamHandler(sys.stdout)
        stream_handler.flush = sys.stdout.flush  # type: ignore
        stream_handler.setFormatter(sky_logging.DIM_FORMATTER)
        stream_handler.setLevel(logging.WARNING)

        provision_logger.addHandler(file_handler)
        provision_logger.addHandler(stream_handler)

        config.log_path = log_abs_path
        yield
    finally:
        provision_logger.removeHandler(file_handler)
        provision_logger.removeHandler(stream_handler)
        stream_handler.close()


def get_log_path() -> pathlib.Path:
    return config.log_path
