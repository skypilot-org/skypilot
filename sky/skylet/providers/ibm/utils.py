"""holds common utility function/constants to be used by the providers."""

import logging
import time
from pathlib import Path

RAY_RECYCLABLE = "ray-recyclable"


def get_logger(caller_name):
    """
    Configures the logger of this module for console output and file output
    logs of level DEBUG and higher will be directed to file under LOGS_FOLDER.
    logs of level INFO and higher will be directed to console output.
    """
    logger = logging.getLogger(caller_name)
    LOGS_FOLDER = "/tmp/connector_logs/"  # this node_provider's logs location.
    logger.setLevel(logging.DEBUG)

    Path(LOGS_FOLDER).mkdir(parents=True, exist_ok=True)
    logs_path = LOGS_FOLDER + caller_name + time.strftime("%Y-%m-%d--%H-%M-%S")
    # pylint: disable=line-too-long
    file_formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(logs_path)
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)

    console_output_handler = logging.StreamHandler()
    console_output_handler.setFormatter(file_formatter)
    console_output_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_output_handler)
    return logger
