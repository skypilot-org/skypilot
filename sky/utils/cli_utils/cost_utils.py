"""Utilities for sky cost report and sky spot cost."""
from typing import Any, Dict, List
from sky import global_user_state


def aggregate_all_records(condensed: bool) -> List[Dict[str, Any]]:
    rows = global_user_state.get_distinct_cluster_names_from_history()
    records = global_user_state.get_clusters_from_history()

    agg_records: List[Dict[str, Any]] = []

    for (cluster_name,) in rows:
        if condensed:
            agg_records.append(_aggregate_records_by_name(
                cluster_name, records))
        else:
            agg_records += _get_non_condensed_records_by_name(
                cluster_name, records)

    return agg_records


def _aggregate_records_by_name(cluster_name: str,
                               records: List[Any]) -> Dict[str, Any]:
    agg_record: Dict[str, Any] = {}

    for record in records:

        if record['name'] == cluster_name:
            if not agg_record:
                agg_record = {
                    'name': record['name'],
                    'launched_at': record['launched_at'],
                    'duration': record['duration'],
                    'num_nodes': record['num_nodes'],
                    'resources': record['resources'],
                    'cluster_hash': record['cluster_hash'],
                    'usage_intervals': record['usage_intervals'],
                }
            else:
                agg_record['duration'] += record['duration']
                agg_record['usage_intervals'] += record['usage_intervals']
                agg_record['resources'] = record['resources']
                agg_record['num_nodes'] = record['num_nodes']

    return agg_record


def _get_non_condensed_records_by_name(
        cluster_name: str, records: List[Any]) -> List[Dict[str, Any]]:

    agg_records: List[Dict[str, Any]] = []
    total_duration = 0
    num_recoveries = 0
    min_launch_time = float('inf')

    for record in records:

        if record['name'] == cluster_name:
            agg_record = {
                'name': record['name'],
                'job_id': '',
                'num_nodes': record['num_nodes'],
                'resources': record['resources'],
                'cluster_hash': record['cluster_hash'],
                'launched_at': record['launched_at'],
                'duration': record['duration'],
                'usage_intervals': record['usage_intervals'],
                'num_recoveries': 0,
            }

            total_duration += record['duration']
            num_recoveries += 1
            min_launch_time = min(min_launch_time, record['launched_at'])

            agg_records.append(agg_record)

    if len(agg_records) == 1:
        return agg_records

    head_record = {}

    for k, v in agg_records[0].items():
        head_record[k] = v

    head_record['duration'] = total_duration
    head_record['num_recoveries'] = num_recoveries
    head_record['launched_at'] = min_launch_time - 1

    agg_records = [head_record] + agg_records

    return agg_records


def get_total_cost(cluster_report: Dict[str, Any]) -> float:
    duration = cluster_report['duration']
    launched_nodes = cluster_report['num_nodes']
    launched_resources = cluster_report['resources']

    cost = (launched_resources.get_cost(duration) * launched_nodes)
    return cost
