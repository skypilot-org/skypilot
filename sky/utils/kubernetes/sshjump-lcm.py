import os
import datetime
import pytz
import time
from kubernetes import client, config

# Load kube config
config.load_incluster_config()

v1 = client.CoreV1Api()

# Get the current namespace from the pod service account
with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
    current_namespace = f.read()

# Set the time delta for checking last active pods
time_delta = datetime.timedelta(minutes=10)

# Set delay for each retry
retry_delay = 60  # In seconds

while True:
    # Get the current time
    now = datetime.datetime.now(pytz.UTC)

    found = False
    # List the pods in the current namespace
    ret = v1.list_namespaced_pod(current_namespace)
    for i in ret.items:
        if i.metadata.labels and 'parent' in i.metadata.labels and i.metadata.labels['parent'] == 'skypilot':
            # Calculate the elapsed time since the pod was last active
            elapsed_time = now - i.metadata.creation_timestamp
            # If the pod was active in the last 10 minutes, set found to True
            if elapsed_time < time_delta:
                found = True
                break

    # If no active pods were found with the specified label, exit the script
    if not found:
        print("No active pods found with label 'parent: skypilot' in the past 10 minutes. Exiting...")
        exit(1)

    # If pods were found, sleep for the specified delay and then retry
    print(f"Active pods found with label 'parent: skypilot' in namespace: '{current_namespace}'. Retrying in {retry_delay} seconds...")
    time.sleep(retry_delay)