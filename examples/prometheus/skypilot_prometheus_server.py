# A prometheus metric server that exposes various SkyPilot metrics
#
# Usage: python skypilot_prometheus_server.py --port 8000
#
# Metrics exposed:
# num_clusters: Number of clusters currently running
# num_nodes: Number of nodes currently running
# num_spot_jobs: Number of total spot jobs
# num_running_spot_jobs: Number of running spot jobs
# num_failed_spot_jobs: Number of spot jobs that failed
# num_succeeded_spot_jobs: Number of spot jobs that succeeded
# num_preemptions_spot_jobs: Total number of preemptions that occurred across all jobs

import argparse
from prometheus_client import start_http_server, Gauge
import time

from sky import core
from sky import exceptions
from sky import global_user_state
from sky.spot import spot_state

metric_num_clusters = Gauge('num_clusters', 'Number of clusters currently running')
metric_num_nodes = Gauge('num_nodes', 'Number of nodes currently running')
metric_num_spot_jobs = Gauge('num_spot_jobs', 'Number of total spot jobs.')
metric_num_running_spot_jobs = Gauge('num_running_spot_jobs', 'Number of running spot jobs.')
metric_num_failed_spot_jobs = Gauge('num_failed_spot_jobs', 'Number of spot jobs that failed')
metric_num_succeeded_spot_jobs = Gauge('num_succeeded_spot_jobs', 'Number of spot jobs that succeeded')
metric_num_preemptions_spot_jobs = Gauge('num_preemptions_spot_jobs', 'Total number of preemptions that occurred across all jobs.')

SPOT_FAIL_STATES = [spot_state.SpotStatus.FAILED,
                    spot_state.SpotStatus.FAILED_SETUP,
                    spot_state.SpotStatus.FAILED_PRECHECKS,
                    spot_state.SpotStatus.FAILED_NO_RESOURCE,
                    spot_state.SpotStatus.FAILED_CONTROLLER]


def update_cluster_count():
    """
    Simulate updating the cluster count. Replace this with your actual logic
    to fetch the number of running clusters.
    """
    print("Updating metrics")
    clusters = global_user_state.get_clusters()
    # Calculate on-demand metrics
    n_clusters = len(clusters)
    num_nodes = 0
    for cluster in clusters:
        num_nodes += cluster['handle'].launched_nodes

    # Update on-demand metrics
    print(f"Number of clusters: {n_clusters}")
    print(f"Number of nodes: {num_nodes}")
    metric_num_clusters.set(n_clusters)
    metric_num_nodes.set(num_nodes)

    # Calculate spot job metrics
    try:
        spot_jobs = core.spot_queue(refresh=True)
    except exceptions.ClusterNotUpError:
        print("Spot controller not up, skipping spot metrics update")
        pass
    else:
        n_spot_jobs = len(spot_jobs)
        n_running_spot_jobs = len([job for job in spot_jobs if job['status'] == spot_state.SpotStatus.RUNNING])
        n_failed_spot_jobs = len([job for job in spot_jobs if job['status'] in SPOT_FAIL_STATES])
        n_succeeded_spot_jobs = len([job for job in spot_jobs if job['status'] == spot_state.SpotStatus.SUCCEEDED])
        n_preemptions = sum(job['recovery_count'] for job in spot_jobs)
        print(f"Number of spot jobs: {n_spot_jobs}")
        print(f"Number of running spot jobs: {n_running_spot_jobs}")
        print(f"Number of failed spot jobs: {n_failed_spot_jobs}")
        print(f"Number of succeeded spot jobs: {n_succeeded_spot_jobs}")
        print(f"Number of preemptions: {n_preemptions}")
        metric_num_spot_jobs.set(n_spot_jobs)
        metric_num_running_spot_jobs.set(n_running_spot_jobs)
        metric_num_failed_spot_jobs.set(n_failed_spot_jobs)
        metric_num_succeeded_spot_jobs.set(n_succeeded_spot_jobs)
        metric_num_preemptions_spot_jobs.set(n_preemptions)


def main(port: int, addr: str):
    start_http_server(port, addr=addr)
    while True:
        try:
            update_cluster_count()
        except Exception as e:
            print(f"Error updating metrics: {e}")
        time.sleep(60)  # Update the metric every 60 seconds


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=9000, help='Port to run metrics server on.')
    parser.add_argument('--addr', type=str, default='0.0.0.0', help='Address to expose the server on.')
    args = parser.parse_args()
    main(args.port, args.addr)
