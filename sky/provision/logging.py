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

# Provisions can overlap in one process (threads), and the sky.provision
# logger is process-wide: keep propagation off while any is active, and
# restore it when the last one exits.
_propagation_lock = threading.Lock()
_active_provisions = 0
_saved_propagate = True


@contextlib.contextmanager
def setup_provision_logging(log_dir: str):
    global _active_provisions, _saved_propagate
    provision_logger = logging.getLogger('sky.provision')
    with _propagation_lock:
        if _active_provisions == 0:
            _saved_propagate = provision_logger.propagate
        _active_provisions += 1
        # Disable propagation to avoid streaming logs to the console, which
        # is set up for sky root logger.
        provision_logger.propagate = False
    provisioner_logger = logging.getLogger('sky.provisioner')
    fh = None
    stream_handler = None
    try:
        # Redirect underlying provision logs to file.
        log_path = os.path.expanduser(os.path.join(log_dir, 'provision.log'))
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log_abs_path = pathlib.Path(log_path).expanduser().absolute()
        fh = logging.FileHandler(log_abs_path)
        fh.setFormatter(sky_logging.FORMATTER)
        fh.setLevel(logging.DEBUG)

        # Add the file handler to the sky.provision.provisioner logger, so the
        # time stamps are logged. We use sky.provisioner for getting the logger
        # because we do not want it to be affected by the handler added to the
        # sky.provision logger. Refer to sky.provision.provisioner.
        provisioner_logger.addHandler(fh)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.flush = sys.stdout.flush  # type: ignore
        stream_handler.setFormatter(sky_logging.DIM_FORMATTER)
        stream_handler.setLevel(logging.WARNING)
        provision_logger.addHandler(fh)
        provision_logger.addHandler(stream_handler)

        config.log_path = log_abs_path
        yield
    finally:
        # First, so a failing cleanup below cannot leave the count raised.
        with _propagation_lock:
            _active_provisions -= 1
            if _active_provisions == 0:
                # Otherwise every later sky.provision.* log in this process
                # outside a provision goes nowhere: no handler, no propagation.
                provision_logger.propagate = _saved_propagate
        # Guarded, so a failure while setting up is not masked by a NameError.
        if fh is not None:
            provisioner_logger.removeHandler(fh)
            provision_logger.removeHandler(fh)
            fh.close()
        if stream_handler is not None:
            provision_logger.removeHandler(stream_handler)
            stream_handler.close()


def get_log_path() -> pathlib.Path:
    return config.log_path
