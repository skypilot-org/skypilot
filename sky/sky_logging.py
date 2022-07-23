"""Logging utilities."""
import logging
import sys

from sky.utils import env_options

# If the SKYPILOT_MINIMIZE_LOGGING environment variable is set to True,
# remove logging prefixes and unnecessary information in optimizer
FORMAT = (None if env_options.Options.MINIMIZE_LOGGING.get() else
          '%(levelname).1s %(asctime)s %(filename)s:%(lineno)d] %(message)s')
DATE_FORMAT = '%m-%d %H:%M:%S'


class NewLineFormatter(logging.Formatter):
    """Adds logging prefix to newlines to align multi-line messages."""

    def __init__(self, fmt, datefmt=None):
        logging.Formatter.__init__(self, fmt, datefmt)

    def format(self, record):
        msg = logging.Formatter.format(self, record)
        if record.message != '':
            parts = msg.split(record.message)
            msg = msg.replace('\n', '\r\n' + parts[0])
        return msg


def init_logger(name: str):
    h = logging.StreamHandler(sys.stdout)
    h.flush = sys.stdout.flush

    fmt = NewLineFormatter(FORMAT, datefmt=DATE_FORMAT)
    h.setFormatter(fmt)

    logger = logging.getLogger(name)
    logger.addHandler(h)
    if env_options.Options.SHOW_DEBUG_INFO.get():
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    return logger
