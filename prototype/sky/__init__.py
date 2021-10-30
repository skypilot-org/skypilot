from sky import clouds
from sky.dag import Dag, DagContext
from sky.execution import execute
from sky.resources import Resources
from sky.task import Task
from sky.registry import fill_in_launchable_resources
from sky.optimizer import Optimizer
import sys
import logging


__all__ = [
    'Dag',
    'DagContext',
    'Optimizer',
    'Resources',
    'Task',
    'execute',
    'fill_in_launchable_resources',
]

class NewLineFormatter(logging.Formatter):
    def __init__(self, fmt, datefmt=None):
        logging.Formatter.__init__(self, fmt, datefmt)

    def format(self, record):
        msg = logging.Formatter.format(self, record)
        if record.message != "":
            parts = msg.split(record.message)
            msg = msg.replace('\n', '\n' + parts[0])
        return msg
h = logging.StreamHandler(sys.stdout)
h.flush = sys.stdout.flush
FORMAT = '%(asctime)s | %(levelname)-6s | %(name)-30sL%(lineno)-5d || %(message)s'
fmt = NewLineFormatter(FORMAT, datefmt='%m-%d %H:%M:%S')
h.setFormatter(fmt)
logging.basicConfig(
    level=logging.INFO,
    handlers=[h],
)