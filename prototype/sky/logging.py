import sys
import logging


class NewlineFormatter(logging.Formatter):
    def __init__(self, fmt, datefmt=None):
        logging.Formatter.__init__(self, fmt, datefmt)

    def format(self, record):
        msg = logging.Formatter.format(self, record)
        if record.message != '':
            parts = msg.split(record.message)
            msg = msg.rstrip(' ')
            last = msg[-1]
            msg = msg[:-1].replace('\n', '\n' + parts[0])
            msg += last
        return msg

FORMAT = '%(levelname).1s %(asctime)s %(filename)s:%(lineno)-3d] %(message)s'
DATE_FORMAT = '%m-%d %H:%M:%S'

newline_handler = logging.StreamHandler(sys.stdout)
newline_handler.flush = sys.stdout.flush
fmt = NewlineFormatter(FORMAT, datefmt=DATE_FORMAT)
newline_handler.setFormatter(fmt)


def init_logger(name):
    logger = logging.getLogger(name)
    logger.addHandler(newline_handler)
    logger.setLevel(logging.DEBUG)
    return logger

def enable_newline(logger):
    logger.handlers[0].terminator = '\n'
    return logger


def disable_newline(logger):
    logger.handlers[0].terminator = ''
    return logger
