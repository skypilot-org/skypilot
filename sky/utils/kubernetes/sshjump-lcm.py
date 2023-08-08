import os
import datetime
import pytz
import time
import sys

from kubernetes import client, config

# Load kube config
config.load_incluster_config()

v1 = client.CoreV1Api()

current_name = os.getenv("MY_POD_NAME")
current_namespace = os.getenv("MY_POD_NAMESPACE")

# In seconds
alert_threshold = int(os.getenv("ALERT_THRESHOLD", "300"))
# In seconds
retly_interval = int(os.getenv("RETRY_INTERVAL", "60"))

label_selector=f"skypilot-sshjump={current_name}"


def poll():
    sys.stdout.write("enter poll()\n")

    # Set alert threshold. This is the amount of time where no ray pods exist
    # and this sshjump pod and service will get terminated
    alert_delta = datetime.timedelta(seconds=alert_threshold)

    # Set delay for each retry
    retry_interval_delta = datetime.timedelta(seconds=retly_interval)

    # Accumulated wait time to be compared with alert threshold time
    w8time_delta = datetime.timedelta()

    while True:
        time.sleep(retly_interval)
    
        # List the pods in the current namespace
        try:
            ret = v1.list_namespaced_pod(current_namespace, label_selector=label_selector)
        except Exception as e:
            sys.stdout.write(f"[ERROR] exit poll() with error: {e}\n")
            raise

        if len(ret.items) == 0:
            sys.stdout.write(f"NOT FOUND active pods with label '{label_selector}' in namespace: '{current_namespace}'\n")
            w8time_delta = w8time_delta + retry_interval_delta
            sys.stdout.write(f"w8time_delta after time increment: {w8time_delta}, alert threshold: {alert_delta}\n")
        else:
            sys.stdout.write(f"FOUND active pods with label '{label_selector}' in namespace: '{current_namespace}'\n")
            # reset ..
            w8time_delta = datetime.timedelta()
            sys.stdout.write(f"w8time_delta is reset: {w8time_delta}\n")
    
        if w8time_delta >= alert_delta:
            sys.stdout.write(f"w8time_delta: {w8time_delta} crossed alert threshold: {alert_delta}. It's time to terminate myself\n")
            try:
                # NOTE: according to template all sshjump resources under
                # same name
                v1.delete_namespaced_service(current_name, current_namespace)
                v1.delete_namespaced_pod(current_name, current_namespace)
            except Exception as e:
                sys.stdout.write(f"[ERROR] exit poll() with error: {e}\n")
                raise

            sys.stdout.write("exit poll()\n")
            break


def main():
    sys.stdout.write("enter main()\n")
    sys.stdout.write(f"*** current_name {current_name}\n")
    sys.stdout.write(f"*** current_namespace {current_namespace}\n")
    sys.stdout.write(f"*** alert_threshold {alert_threshold}\n")
    sys.stdout.write(f"*** retly_interval {retly_interval}\n")
    sys.stdout.write(f"*** label_selector {label_selector}\n")

    if not current_name or not current_namespace:
        raise Exception('[ERROR] One or more environment variables is missing '
                        'with an actual value.')
    poll()


if __name__ == '__main__':
    main()
