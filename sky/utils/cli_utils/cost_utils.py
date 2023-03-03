"""Utilities for sky cost report and sky spot cost."""
from typing import Any, Dict, List
import colorama
from sky import global_user_state


def get_cost_report(cluster_status):
    cost = cluster_status['total_cost']

    if not cost:
        return '-'

    return f'${cost:.3f}'


def get_status_for_cost_report(cluster_status):
    status = None
    if 'status' in cluster_status:
        status = cluster_status['status']

    if status is None:
        return f'{colorama.Style.DIM}{"TERMINATED"}{colorama.Style.RESET_ALL}'
    return status.colored_str()


def get_resources_for_cost_report(cluster_status):
    launched_nodes = cluster_status['num_nodes']
    launched_resources = cluster_status['resources']

    launched_resource_str = str(launched_resources)
    resources_str = (f'{launched_nodes}x '
                     f'{launched_resource_str}')

    return resources_str


def aggregate_all_records(verbose: bool) -> List[Dict[str, Any]]:
    rows = global_user_state.get_distinct_cluster_names_from_history()
    records = global_user_state.get_clusters_from_history()

    agg_records: List[Dict[str, Any]] = []

    for (cluster_name,) in rows:
        if verbose:
            agg_records += get_split_view_records_by_name(cluster_name, records)
        else:
            agg_records.append(aggregate_records_by_name(cluster_name, records))

    return agg_records


def aggregate_records_by_name(cluster_name: str,
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


def get_split_view_records_by_name(cluster_name: str,
                                   records: List[Any]) -> List[Dict[str, Any]]:

    agg_records: List[Dict[str, Any]] = []
    total_duration = 0

    for record in records:

        if record['name'] == cluster_name:
            agg_record = {
                'name': '',
                'job_id': '',
                'num_nodes': '',
                'resources': '',
                'cluster_hash': record['cluster_hash'],
                'launched_at': record['launched_at'],
                'duration': record['duration'],
                'usage_intervals': record['usage_intervals'],
            }

            total_duration += record['duration']

            agg_records.append(agg_record)

            if len(agg_records) == 0:
                agg_record['name'] = record['name']
                agg_record['num_nodes'] = record['num_nodes']
                agg_record['resources'] = record['resources']

                agg_records.append(agg_record)
                agg_records[0], agg_records[1] = agg_records[1], agg_records[0]

    agg_records[0]['duration'] = total_duration
    return agg_records


def get_total_cost(cluster_report: Dict[str, Any]) -> float:
    duration = cluster_report['duration']
    launched_nodes = cluster_report['num_nodes']
    launched_resources = cluster_report['resources']

    cost = (launched_resources.get_cost(duration) * launched_nodes)
    return cost
