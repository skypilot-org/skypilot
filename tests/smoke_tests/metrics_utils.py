import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

from prometheus_client import parser
import requests

from sky.utils import log_utils


def collect_metrics(
    url: str,
    metric_name: str,
    duration_seconds: Optional[int] = None,
    stop_event: Optional[threading.Event] = None,
    interval_seconds: int = 5
) -> Dict[Tuple[str, ...], List[Tuple[float, float]]]:
    assert (duration_seconds is not None) ^ (
        stop_event is not None
    ), "Exactly one of duration_seconds or stop_event must be provided"

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


def parse_metrics(
        metric_name: str,
        metrics_text: str) -> Dict[Tuple[str, ...], List[Tuple[float, float]]]:
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


def compare_rss_metrics(baseline: Dict[Tuple[str, ...], List[Tuple[float,
                                                                   float]]],
                        test: Dict[Tuple[str, ...], List[Tuple[float, float]]]):
    comparison = {}
    all_keys = set(baseline.keys()) | set(test.keys())
    for key in all_keys:
        key_label = ':'.join(key)
        baseline_values = baseline.get(key, [])
        test_values = test.get(key, [])

        baseline_peak = max([v for _, v in baseline_values
                            ]) if baseline_values else 0
        test_peak = max([v for _, v in test_values]) if test_values else 0

        increase_bytes = test_peak - baseline_peak
        increase_pct = (increase_bytes / baseline_peak *
                        100) if baseline_peak > 0 else (
                            float('inf') if test_peak > 0 else 0)

        comparison[key_label] = {
            'baseline_peak_rss': baseline_peak,
            'test_peak_rss': test_peak,
            'increase_bytes': increase_bytes,
            'increase_percent': increase_pct,
        }

    print(f"SUMMARY", file=sys.stderr, flush=True)

    table = log_utils.create_table(
        ['PROCESS', 'BASELINE RSS', 'TEST RSS', 'INCREASE', '%'])
    table.align = 'r'
    table.align['PROCESS'] = 'l'

    failed_checks = []
    total_baseline = total_test = 0

    for key_label, data in comparison.items():
        baseline_peak = data['baseline_peak_rss']
        test_peak = data['test_peak_rss']
        increase_bytes = data['increase_bytes']
        increase_pct = data['increase_percent']

        baseline_mb = baseline_peak / (1024 * 1024)
        test_mb = test_peak / (1024 * 1024)
        increase_mb = increase_bytes / (1024 * 1024)
        is_new_process = baseline_peak == 0

        # Add to table
        table.add_row([
            key_label, f"{baseline_mb:.1f} MB", f"{test_mb:.1f} MB",
            f"{increase_mb:+.1f} MB",
            f"{increase_pct:+.1f}%" if increase_pct != float('inf') else "+inf%"
        ])

        if test_mb > 300:
            failed_checks.append(
                f"Process {key_label} exceeded 300 MB: {test_mb:.1f} MB")
        if increase_pct > 50 and baseline_peak > 0:
            failed_checks.append(
                f"Process {key_label} increased by {increase_pct:.1f}% (limit: 50%)"
            )
        total_baseline += baseline_peak
        total_test += test_peak

    total_baseline_mb = total_baseline / (1024 * 1024)
    total_test_mb = total_test / (1024 * 1024)
    total_increase_mb = (total_test - total_baseline) / (1024 * 1024)
    total_increase_pct = (total_test - total_baseline
                         ) / total_baseline * 100 if total_baseline > 0 else 0
    if total_increase_pct > 20:
        failed_checks.append(
            f"Average memory increase too high: {total_increase_pct:.1f}% (limit: 20%)"
        )
    table.add_row([
        "TOTAL", f"{total_baseline_mb:.1f} MB", f"{total_test_mb:.1f} MB",
        f"{total_increase_mb:+.1f} MB", f"{total_increase_pct:+.1f}%"
    ])

    print(table.get_string(), file=sys.stderr, flush=True)

    # Print regression check results
    if failed_checks:
        raise Exception(f"Performance regression detected: {failed_checks}")
