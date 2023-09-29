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
def setup_provision_logging(log_dir: str):
    try:
        # Redirect underlying provision logs to file.
        log_path = os.path.join(log_dir, 'provision.log')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log_abs_path = pathlib.Path(log_path).expanduser().absolute()
        fh = logging.FileHandler(log_abs_path)
        fh.setFormatter(sky_logging.FORMATTER)
        fh.setLevel(logging.DEBUG)

        # Add the file handler to the provisioner logger, so the time stamps are
        # logged.
        provisioner_logger = logging.getLogger('sky.provisioner')
        provisioner_logger.addHandler(fh)

        provision_logger = logging.getLogger('sky.provision')
        stream_handler = sky_logging.RichSafeStreamHandler(sys.stdout)
        stream_handler.flush = sys.stdout.flush  # type: ignore
        stream_handler.setFormatter(sky_logging.DIM_FORMATTER)
        stream_handler.setLevel(logging.WARNING)

        config.log_path = log_abs_path
        yield
    finally:
        provisioner_logger.removeHandler(fh)
        provision_logger.removeHandler(fh)
        provision_logger.removeHandler(stream_handler)
        stream_handler.close()
        fh.close()


def get_log_path() -> pathlib.Path:
    return config.log_path
