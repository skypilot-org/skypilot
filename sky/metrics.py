"""Prometheus Metric Collection Module"""
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
import time
import functools
from sky import cloud_logging
from sky.utils import base_utils

PROM_PUSHGATEWAY_URL = '3.216.190.117:9091'
CURRENT_CLUSTER_NAME = 'NONE'


class Label:
    """
    Label for prometheus metric
    """

    def __init__(self, name):
        self.name = name
        self.val = 'null'

    def set_value(self, val):
        self.val = val


class Metric:
    """
    Single prometheus Gauge metric
    """

    def __init__(self, name, desc):
        self.name = name
        self.desc = desc
        self.val = 0

    def make_prom(self, label_names):
        self.prom_metric = Gauge(self.name, self.desc, label_names)

    def register(self, registry):
        registry.register(self.prom_metric)

    def set_value(self, val):
        self.val = val

    def update_prom_metric(self, labels):
        self.prom_metric.labels(*labels).set(self.val)


class MetricLogger:
    """
    Provides decorator to wrap functions that need to be logged
    """

    def __init__(self,
                 func_name,
                 labels=None,
                 metrics=None,
                 with_cluster_name=False,
                 with_return_code=False,
                 with_runtime=False):

        if not labels:
            labels = []
        if not metrics:
            metrics = []

        self.func_name = func_name
        self.labels = labels
        self.metrics = metrics
        self.with_cluster_name = with_cluster_name
        self.with_runtime = with_runtime

        self.labels.append(Label('user'))

        if with_cluster_name:
            self.labels.append(Label('cluster_name'))

        if with_return_code:
            self.return_code_metric = self.func_name + '_return_code'
            self.metrics.append(
                Metric(self.return_code_metric,
                       f'Return code for {self.func_name}'))

        if with_runtime:
            self.runtime_metric = self.func_name + '_runtime'
            self.metrics.append(
                Metric(self.runtime_metric, f'Runtime for {self.func_name}'))

        self.label_dict = {e.name: e for e in self.labels}
        self.metric_dict = {e.name: e for e in self.metrics}

        self.registry = CollectorRegistry()

        labels_list = [e.name for e in labels]
        for metric in self.metrics:
            metric.make_prom(labels_list)
            metric.register(self.registry)

        def decorator(func):

            @functools.wraps(func)
            def wrapper_logging(*args, **kwargs):
                self.set_labels({'user': base_utils.get_user()})
                saved_ex = None
                try:
                    start = time.time()
                    func(*args, **kwargs)
                    if self.with_runtime:
                        self.set_metrics(
                            {self.runtime_metric: time.time() - start})
                    if self.with_cluster_name:
                        self.set_labels({'cluster_name': CURRENT_CLUSTER_NAME})
                except Exception as ex:  # pylint: disable=broad-except
                    if with_return_code:
                        self.set_return_code(-1)
                    cloud_logging.send_trace()
                    saved_ex = ex
                labels = [e.val for e in self.labels]
                for metric in metrics:
                    metric.update_prom_metric(labels)
                push_to_gateway(PROM_PUSHGATEWAY_URL,
                                job=f'{base_utils.get_user()} {self.func_name}',
                                registry=self.registry)
                if saved_ex:
                    raise saved_ex

            return wrapper_logging

        self.decorator = decorator

    def set_labels(self, labels_dict):
        for label_name in labels_dict:
            self.label_dict[label_name].set_value(labels_dict[label_name])

    def set_metrics(self, metrics_dict):
        for metric_name in metrics_dict:
            self.metric_dict[metric_name].set_value(metrics_dict[metric_name])

    def set_return_code(self, value):
        self.set_metrics({self.return_code_metric: value})


#### USER METRICS ###
### Labels: User ID, Cluster ID


class TimerLogger(MetricLogger):

    def __init__(self, func_name):
        super().__init__(func_name, with_cluster_name=True, with_runtime=True)

    def __call__(self, func):
        return self.decorator(func)


#### USER METRICS ###
### Labels: User ID


class ReturnCodeLogger(MetricLogger):

    def __init__(self, func_name):
        super().__init__(func_name, with_return_code=True)

    def __call__(self, func):
        return self.decorator(func)
