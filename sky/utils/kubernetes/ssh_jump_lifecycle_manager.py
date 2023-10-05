"""Manages lifecycle of ssh jump pod.

This script runs inside ssh jump pod as the main process (PID 1).

It terminates itself (by removing ssh jump service and pod via a call to
kubeapi), if it does not see ray pods in the duration of 10 minutes. If the
user re-launches a task before the duration is over, then ssh jump pod is being
reused and will terminate itself when it sees that no ray cluster exist in that
duration.
"""
import datetime
import os
import sys
import time

from kubernetes import client
from kubernetes import config

# Load kube config
config.load_incluster_config()

v1 = client.CoreV1Api()

current_name = os.getenv('MY_POD_NAME')
current_namespace = os.getenv('MY_POD_NAMESPACE')

# The amount of time in seconds where no Ray pods exist in which after that time
# ssh jump pod terminates itself
alert_threshold = int(os.getenv('ALERT_THRESHOLD', '600'))
# The amount of time in seconds to wait between Ray pods existence checks
retry_interval = int(os.getenv('RETRY_INTERVAL', '60'))

# Ray pods are labeled with this value i.e., ssh jump name which is unique per
# user (based on user hash)
label_selector = f'skypilot-ssh-jump={current_name}'


def poll():
    sys.stdout.write('Starting polling.\n')

    alert_delta = datetime.timedelta(seconds=alert_threshold)

    # Set delay for each retry
    retry_interval_delta = datetime.timedelta(seconds=retry_interval)

    # Accumulated time of where no SkyPilot cluster exists. Used to compare
    # against alert_threshold
    nocluster_delta = datetime.timedelta()

    while True:
        sys.stdout.write(f'Sleeping {retry_interval} seconds..\n')
        time.sleep(retry_interval)

        # List the pods in the current namespace
        try:
            ret = v1.list_namespaced_pod(current_namespace,
                                         label_selector=label_selector)
        except Exception as e:
            sys.stdout.write(f'Error: listing pods failed with error: {e}\n')
            raise

        if len(ret.items) == 0:
            sys.stdout.write(f'Did not find pods with label "{label_selector}" '
                             f'in namespace {current_namespace}\n')
            nocluster_delta = nocluster_delta + retry_interval_delta
            sys.stdout.write(
                f'Time since no pods found: {nocluster_delta}, alert '
                f'threshold: {alert_delta}\n')
        else:
            sys.stdout.write(
                f'Found pods with label "{label_selector}" in namespace '
                f'{current_namespace}\n')
            # reset ..
            nocluster_delta = datetime.timedelta()
            sys.stdout.write(f'noray_delta is reset: {nocluster_delta}\n')

        if nocluster_delta >= alert_delta:
            sys.stdout.write(
                f'nocluster_delta: {nocluster_delta} crossed alert threshold: '
                f'{alert_delta}. Time to terminate myself and my service.\n')
            try:
                # ssh jump resources created under same name
                v1.delete_namespaced_service(current_name, current_namespace)
                v1.delete_namespaced_pod(current_name, current_namespace)
            except Exception as e:
                sys.stdout.write('[ERROR] Deletion failed. Exiting '
                                 f'poll() with error: {e}\n')
                raise

            break

    sys.stdout.write('Done polling.\n')


def main():
    sys.stdout.write('SkyPilot SSH Jump Pod Lifecycle Manager\n')
    sys.stdout.write(f'current_name: {current_name}\n')
    sys.stdout.write(f'current_namespace: {current_namespace}\n')
    sys.stdout.write(f'alert_threshold time: {alert_threshold}\n')
    sys.stdout.write(f'retry_interval time: {retry_interval}\n')
    sys.stdout.write(f'label_selector: {label_selector}\n')

    if not current_name or not current_namespace:
        # Raise Exception with message to terminate pod
        raise Exception('Missing environment variables MY_POD_NAME or '
                        'MY_POD_NAMESPACE')
    poll()


if __name__ == '__main__':
    main()
