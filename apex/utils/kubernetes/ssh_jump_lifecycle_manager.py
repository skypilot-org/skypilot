"""Manages lifecycle of ssh jump pod.

This script runs inside ssh jump pod as the main process (PID 1).

It terminates itself (by removing ssh jump service and pod via a call to
kubeapi) if it does not see ray pods in the duration of 10 minutes. If the
user re-launches a task before the duration is over, then ssh jump pod is being
reused and will terminate itself when it sees that no ray clusters exist in
that duration.

To allow multiple users to the share the same SSH jump pod,
this script also reloads SSH keys from the mounted secret volume on an
interval and updates `~/.ssh/authorized_keys`.
"""
import datetime
import os
import subprocess
import sys
import threading
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
# The amount of time in seconds to wait between SSH key reloads
reload_interval = int(os.getenv('RELOAD_INTERVAL', '5'))

# Ray pods are labeled with this value i.e., ssh jump name which is unique per
# user (based on user hash)
label_selector = f'skypilot-ssh-jump={current_name}'


def poll(interval, leading=True):
    """Decorator factory for polling function. To stop polling, return True.

    Args:
        interval (int): The amount of time to wait between function calls.
        leading (bool): Whether to wait before (rather than after) calls.
    """

    def decorator(func):

        def wrapper(*args, **kwargs):
            while True:
                if leading:
                    time.sleep(interval)
                done = func(*args, **kwargs)
                if done:
                    return
                if not leading:
                    time.sleep(interval)

        return wrapper

    return decorator


# Flag to terminate the reload keys thread when the lifecycle thread
# terminates.
terminated = False


@poll(interval=reload_interval, leading=False)
def reload_keys():
    """Reloads SSH keys from mounted secret volume."""

    if terminated:
        sys.stdout.write('[SSH Key Reloader] Terminated.\n')
        return True

    # Reload SSH keys from mounted secret volume if changed.
    tmpfile = '/tmp/sky-ssh-keys'
    try:
        subprocess.check_output(
            f'cat /etc/secret-volume/ssh-publickey* > {tmpfile}', shell=True)
        try:
            subprocess.check_output(f'diff {tmpfile} ~/.ssh/authorized_keys',
                                    shell=True)
            sys.stdout.write(
                '[SSH Key Reloader] No keys changed, continuing.\n')
        except subprocess.CalledProcessError as e:
            if e.returncode == 1:
                sys.stdout.write(
                    '[SSH Key Reloader] Changes detected, reloading.\n')
                subprocess.check_output(f'mv {tmpfile} ~/.ssh/authorized_keys',
                                        shell=True)
            else:
                raise
    except Exception as e:
        sys.stdout.write(
            f'[SSH Key Reloader][ERROR] Failed to reload SSH keys: {e}\n')
        raise


alert_delta = datetime.timedelta(seconds=alert_threshold)
retry_interval_delta = datetime.timedelta(seconds=retry_interval)
# Accumulated time of where no SkyPilot cluster exists. Compared
# against alert_threshold.
nocluster_delta = datetime.timedelta()


@poll(interval=retry_interval)
def manage_lifecycle():
    """Manages lifecycle of ssh jump pod."""

    global terminated, nocluster_delta

    try:
        ret = v1.list_namespaced_pod(current_namespace,
                                     label_selector=label_selector)
    except Exception as e:
        sys.stdout.write('[Lifecycle] [ERROR] listing pods failed with '
                         f'error: {e}\n')
        raise

    if len(ret.items) == 0:
        sys.stdout.write(
            f'[Lifecycle] Did not find pods with label '
            f'"{label_selector}" in namespace {current_namespace}\n')
        nocluster_delta = nocluster_delta + retry_interval_delta
        sys.stdout.write(
            f'[Lifecycle] Time since no pods found: {nocluster_delta}, alert '
            f'threshold: {alert_delta}\n')
    else:
        sys.stdout.write(
            f'[Lifecycle] Found pods with label "{label_selector}" in '
            f'namespace {current_namespace}\n')
        # reset ..
        nocluster_delta = datetime.timedelta()
        sys.stdout.write(
            f'[Lifecycle] nocluster_delta is reset: {nocluster_delta}\n')

    if nocluster_delta >= alert_delta:
        sys.stdout.write(
            f'[Lifecycle] nocluster_delta: {nocluster_delta} crossed alert '
            f'threshold: {alert_delta}. Time to terminate myself and my '
            'service.\n')
        try:
            # ssh jump resources created under same name
            v1.delete_namespaced_service(current_name, current_namespace)
            v1.delete_namespaced_pod(current_name, current_namespace)
        except Exception as e:
            sys.stdout.write('[Lifecycle][ERROR] Deletion failed. Exiting '
                             f'poll() with error: {e}\n')
            raise

        terminated = True
        return True


def main():
    sys.stdout.write('SkyPilot SSH Jump Pod Lifecycle Manager\n')
    sys.stdout.write(f'current_name: {current_name}\n')
    sys.stdout.write(f'current_namespace: {current_namespace}\n')
    sys.stdout.write(f'alert_threshold time: {alert_threshold}\n')
    sys.stdout.write(f'retry_interval time: {retry_interval}\n')
    sys.stdout.write(f'reload_interval time: {reload_interval}\n')
    sys.stdout.write(f'label_selector: {label_selector}\n')

    if not current_name or not current_namespace:
        # Raise Exception with message to terminate pod
        raise Exception('Missing environment variables MY_POD_NAME or '
                        'MY_POD_NAMESPACE')

    threads = [
        threading.Thread(target=manage_lifecycle),
        threading.Thread(target=reload_keys)
    ]
    sys.stdout.write(f'Polling with {len(threads)} threads.\n')
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    sys.stdout.write('Done.\n')


if __name__ == '__main__':
    main()
