"""Prometheus Metric Collection Module"""
from collections import namedtuple
import time
import traceback
from typing import Callable, Dict, Optional, Union

import prometheus_client

from sky import sky_logging
from sky.utils import base_utils
from sky.utils import env_options
from sky.user_stats import utils

logger = sky_logging.init_logger(__name__)

PROM_PUSHGATEWAY_URL = '34.226.138.119:9091'
current_cluster_name = 'NONE'

_Metric = namedtuple('_Metric', ['name', 'desc', 'val', 'unit'])


class MetricLogger:
    """Provides decorator to wrap functions that need to be logged.

    Example usage:
    @MetricLogger.decorator(name='my_metric')
    def my_function():
        pass
    OR
    @MetricLogger.decorator
    def my_function():
        pass
    OR
    with MetricLogger('my_metric') as metric_logger:
        metric_logger.add_metric('my_metric', 'my_metric_desc', 1, 'unit')
    OR
    metric_logger = MetricLogger('my_metric')
    metric_logger.open()
    metric_logger.add_metric('my_metric', 'my_metric_desc', 1, 'unit')
    ...
    metric_logger.close()
    """

    def __init__(self,
                 name: str,
                 extra_labels: Optional[Dict[str, str]] = None,
                 add_runtime: bool = False,
                 add_cluster_name: bool = False):
        self.name = name
        self.labels = utils.get_base_labels()
        self.metrics = dict()

        if extra_labels is not None:
            self.labels.update(extra_labels)

        self.registry = prometheus_client.CollectorRegistry(auto_describe=True)

        self.start_time = None
        self.add_runtime = add_runtime
        self.add_cluster_name = add_cluster_name

    def add_metric(self, name: str, desc: str, val: float, unit: str):
        name = name.replace('.', '_')
        self.metrics[name] = _Metric(name, desc, val, unit)

    def add_labels(self, labels: Dict[str, str]):
        self.labels.update(labels)

    def _create_metrics(self):
        for metric in self.metrics.values():
            prom_metric = prometheus_client.Gauge(metric.name,
                                                  metric.desc,
                                                  self.labels,
                                                  unit=metric.unit,
                                                  registry=self.registry)
            prom_metric.labels(**self.labels).set(metric.val)

    def open(self):
        self.start_time = time.time()
        logger.debug(f'Start metric for {self.name} with transaction id: '
                     f'{base_utils.transaction_id()}')
        return self

    def close(self):
        if env_options.DISABLE_LOGGING:
            return

        if self.add_runtime:
            end_time = time.time()
            self.add_metric(f'{self.name}_runtime',
                            f'Runtime of {self.name}',
                            end_time - self.start_time,
                            unit='seconds')
        if self.add_cluster_name:
            self.add_labels({'cluster_name': current_cluster_name})

        try:
            logger.debug(
                f'Sending metrics for {self.name} with metrics: {self.metrics}')
            self._create_metrics()
            prometheus_client.push_to_gateway(PROM_PUSHGATEWAY_URL,
                                              job=f'{self.name}',
                                              registry=self.registry,
                                              timeout=5)
        except (SystemExit, Exception) as e:  # pylint: disable=broad-except
            logger.debug('Error pushing metrics to prometheus: \n'
                         f'{traceback.format_exc()}\n{e}')

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.close()

    @staticmethod
    def decorator(name_or_fn: Union[str, Callable],
                  extra_labels: Optional[Dict[str, str]] = None):
        return base_utils.make_decorator(MetricLogger,
                                         name_or_fn,
                                         extra_labels=extra_labels,
                                         add_runtime=True)
