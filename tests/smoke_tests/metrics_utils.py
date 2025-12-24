import dataclasses
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple, TypedDict

from prometheus_client import parser
import requests

from sky.utils import log_utils


class AggregatedMetric(TypedDict):
    baseline: float
    actual: float
    unit: str


@dataclasses.dataclass
class PerKeyDiff:
    key_label: str
    baseline: float
    actual: float
    increase: float
    increase_pct: float


@dataclasses.dataclass
class AggregateDiff:
    total_baseline: float
    total_actual: float
    total_increase: float
    total_increase_pct: float


MetricKey = Tuple[str, ...]
TimeSeries = List[Tuple[float, float]]
Failures = List[str]


def collect_metrics(url: str,
                    metric_name: str,
                    duration_seconds: Optional[int] = None,
                    stop_event: Optional[threading.Event] = None,
                    interval_seconds: int = 5) -> Dict[MetricKey, TimeSeries]:
    assert (duration_seconds is not None) ^ (
        stop_event is not None
    ), "Exactly one of duration_seconds or stop_event must be provided"

    metrics = {}
    start_time = time.time()
    while True:
        if duration_seconds is not None and time.time(
        ) - start_time >= duration_seconds:
            break
        if stop_event is not None and stop_event.is_set():
            break

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            metrics = parse_metrics(metric_name, response.text)
        except Exception as e:
            print(f"  Error collecting metrics: {e}",
                  file=sys.stderr,
                  flush=True)

        if stop_event is not None:
            stop_event.wait(interval_seconds)
        else:
            time.sleep(interval_seconds)

    return metrics


def parse_metrics(metric_name: str,
                  metrics_text: str) -> Dict[MetricKey, TimeSeries]:
    metrics_dict = {}
    timestamp = time.time()

    try:
        for family in parser.text_string_to_metric_families(metrics_text):
            if family.name == metric_name:
                for sample in family.samples:
                    labels = sample.labels
                    # Use label values as the key, sorted for consistency
                    key = tuple(sorted(labels.values()))
                    value = float(sample.value)
                    if key not in metrics_dict:
                        metrics_dict[key] = []
                    metrics_dict[key].append((timestamp, value))
    except Exception as e:
        print(f"Error parsing metrics: {e}", file=sys.stderr, flush=True)

    return metrics_dict


def compare_metrics(baseline: Dict[MetricKey, TimeSeries],
                    actual: Dict[MetricKey, TimeSeries],
                    aggregator_fn: Callable[[TimeSeries, TimeSeries],
                                            AggregatedMetric],
                    per_key_checker: Optional[Callable[[PerKeyDiff],
                                                       Failures]] = None,
                    aggregate_checker: Optional[Callable[[AggregateDiff],
                                                         Failures]] = None):
    """
    General function to compare baseline and actual metrics.

    Args:
        baseline: Baseline metrics dict mapping label tuples to (timestamp, value) lists
        actual: Actual metrics dict mapping label tuples to (timestamp, value) lists
        aggregator_fn: Function(baseline_values, actual_values) -> AggregatedMetric
            Aggregates time-series values into a single scalar per metric.
            Examples: peak RSS, p95 latency, mean CPU usage
        per_key_checker: Optional function(PerKeyDiff)
                             -> list of failure messages
        aggregate_checker: Optional function(AggregateDiff)
                               -> list of failure messages
    """
    comparison = {}
    all_keys = set(baseline.keys()) | set(actual.keys())

    for key in all_keys:
        key_label = ':'.join(key)
        baseline_values = baseline.get(key, [])
        actual_values = actual.get(key, [])

        comparison[key_label] = aggregator_fn(baseline_values, actual_values)

    table = log_utils.create_table(
        ['KEY', 'BASELINE', 'ACTUAL', 'INCREASE', '%'])
    table.align = 'r'
    table.align['KEY'] = 'l'

    failed_checks = []
    total_baseline = 0
    total_actual = 0

    for key_label, data in comparison.items():
        baseline_val = data['baseline']
        actual_val = data['actual']
        unit = data['unit']

        baseline_fmt = f"{baseline_val:.1f} {unit}"
        actual_fmt = f"{actual_val:.1f} {unit}"

        increase = actual_val - baseline_val
        increase_pct = (increase / baseline_val *
                        100) if baseline_val > 0 else (
                            float('inf') if actual_val > 0 else 0)

        increase_fmt = f"{increase:+.1f} {unit}"
        increase_pct_fmt = f"{increase_pct:+.1f}%" if increase_pct != float(
            'inf') else "+inf%"

        table.add_row([
            key_label, baseline_fmt, actual_fmt, increase_fmt, increase_pct_fmt
        ])

        if per_key_checker:
            key_failures = per_key_checker(
                PerKeyDiff(key_label, baseline_val, actual_val, increase,
                           increase_pct))
            for failure in key_failures:
                failed_checks.append(f"{key_label}: {failure}")

        total_baseline += baseline_val
        total_actual += actual_val

    total_increase = total_actual - total_baseline
    total_increase_pct = ((total_actual - total_baseline) / total_baseline *
                          100 if total_baseline > 0 else 0)

    total_baseline_fmt = f"{total_baseline:.1f} {unit}"
    total_actual_fmt = f"{total_actual:.1f} {unit}"
    total_increase_fmt = f"{total_increase:+.1f} {unit}"
    total_increase_pct_fmt = f"{total_increase_pct:+.1f}%"

    table.add_row([
        "TOTAL", total_baseline_fmt, total_actual_fmt, total_increase_fmt,
        total_increase_pct_fmt
    ])

    if aggregate_checker:
        aggregate_failures = aggregate_checker(
            AggregateDiff(total_baseline, total_actual, total_increase,
                          total_increase_pct))
        failed_checks.extend(aggregate_failures)

    print(table.get_string(), file=sys.stderr, flush=True)

    if failed_checks:
        raise Exception(f"Performance regression detected: {failed_checks}")


def rss_peak_aggregator(
        baseline_values: List[Tuple[float, float]],
        actual_values: List[Tuple[float, float]]) -> AggregatedMetric:
    """Aggregator for RSS (memory) metrics - computes peak values."""
    baseline_peak_bytes = max([v for _, v in baseline_values
                              ]) if baseline_values else 0
    actual_peak_bytes = max([v for _, v in actual_values
                            ]) if actual_values else 0

    baseline_mb = baseline_peak_bytes / (1024 * 1024)
    actual_mb = actual_peak_bytes / (1024 * 1024)

    return AggregatedMetric(baseline=baseline_mb, actual=actual_mb, unit='MB')
