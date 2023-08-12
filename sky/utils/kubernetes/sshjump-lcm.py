"""Manages lifecycle of sshjump pod.

This script runs inside sshjump pod as the main process (PID 1).
"""
import datetime
import os
import sys
import time

from kubernetes import client, config

# Load kube config
config.load_incluster_config()

v1 = client.CoreV1Api()

current_name = os.getenv("MY_POD_NAME")
current_namespace = os.getenv("MY_POD_NAMESPACE")

# The amount of time in seconds where no Ray pods exist in which after that time
# sshjump pod terminates itself
alert_threshold = int(os.getenv("ALERT_THRESHOLD", "600"))
# The amount of time in seconds to wait between Ray pods existence checks
retry_interval = int(os.getenv("RETRY_INTERVAL", "60"))

# Ray pods are labeled with this value i.e sshjump name which is unique per user (based on userhash)
label_selector=f"skypilot-sshjump={current_name}"


def poll():
    sys.stdout.write("enter poll()\n")

    alert_delta = datetime.timedelta(seconds=alert_threshold)

    # Set delay for each retry
    retry_interval_delta = datetime.timedelta(seconds=retry_interval)

    # Accumulated time of where no Ray pod exist. Used to compare against alert_threshold
    noray_delta = datetime.timedelta()

    while True:
        sys.stdout.write(f"Sleep {retry_interval} seconds..\n")
        time.sleep(retry_interval)

        # List the pods in the current namespace
        try:
            ret = v1.list_namespaced_pod(current_namespace, label_selector=label_selector)
        except Exception as e:
            sys.stdout.write(f"[ERROR] exit poll() with error: {e}\n")
            raise

        if len(ret.items) == 0:
            sys.stdout.write(f"DID NOT FIND pods with label '{label_selector}' in namespace: '{current_namespace}'\n")
            noray_delta = noray_delta + retry_interval_delta
            sys.stdout.write(f"noray_delta after time increment: {noray_delta}, alert threshold: {alert_delta}\n")
        else:
            sys.stdout.write(f"FOUND pods with label '{label_selector}' in namespace: '{current_namespace}'\n")
            # reset ..
            noray_delta = datetime.timedelta()
            sys.stdout.write(f"noray_delta is reset: {noray_delta}\n")

        if noray_delta >= alert_delta:
            sys.stdout.write(f"noray_delta: {noray_delta} crossed alert threshold: {alert_delta}. It's time to terminate myself\n")
            try:
                # sshjump resources created under same name
                v1.delete_namespaced_service(current_name, current_namespace)
                v1.delete_namespaced_pod(current_name, current_namespace)
            except Exception as e:
                sys.stdout.write(f"[ERROR] exit poll() with error: {e}\n")
                raise

            break

    sys.stdout.write("exit poll()\n")


def main():
    sys.stdout.write("enter main()\n")
    sys.stdout.write(f"*** current_name {current_name}\n")
    sys.stdout.write(f"*** current_namespace {current_namespace}\n")
    sys.stdout.write(f"*** alert_threshold time {alert_threshold}\n")
    sys.stdout.write(f"*** retry_interval time {retry_interval}\n")
    sys.stdout.write(f"*** label_selector {label_selector}\n")

    if not current_name or not current_namespace:
        raise Exception('[ERROR] One or more environment variables is missing '
                        'with an actual value.')
    poll()


if __name__ == '__main__':
    main()
