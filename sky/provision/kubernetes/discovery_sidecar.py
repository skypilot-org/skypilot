"""Node discovery server for K8s managed jobs.

This module contains the source code for a lightweight HTTP server that runs
as a background process in each managed job pod. It provides:

  GET /nodes  - Returns JSON with all nodes and their status
  GET /health - Returns 200 OK

On rank-0 (head) pods, /nodes queries the K8s Pods API to aggregate node
status across the job. On non-head pods, /nodes proxies to the head pod.

For job groups, /nodes additionally groups nodes by task name when the
SKYPILOT_JOBGROUP_TASKS env var is set.

The server uses only Python stdlib - no pip dependencies - so it works with
any base image that has Python 3.
"""

# Port for the discovery server (same on all pods).
DISCOVERY_SERVER_PORT = 9876

# The discovery script is stored as a string constant so it can be embedded
# directly in the pod entrypoint. No separate image needed.
DISCOVERY_SERVER_SCRIPT = r'''
import http.server
import json
import os
import ssl
import threading
import time
import urllib.request

SERVICE_NAME = os.environ.get("SKYPILOT_SERVICE_NAME", "")
NAMESPACE = os.environ.get("SKYPILOT_NAMESPACE", "default")
SELF_RANK = int(os.environ.get("SKYPILOT_NODE_RANK", "0"))
NUM_NODES = int(os.environ.get("SKYPILOT_NUM_NODES", "1"))
PORT = int(os.environ.get("SKYPILOT_DISCOVERY_PORT", "9876"))
# Comma-separated task_name:service_name pairs for job groups
JOBGROUP_TASKS = os.environ.get("SKYPILOT_JOBGROUP_TASKS", "")
APP_STATUS_FILE = os.environ.get("SKYPILOT_APP_STATUS", "/tmp/skypilot_app_status")

K8S_API = "https://kubernetes.default.svc"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

IS_HEAD = (SELF_RANK == 0)
# Head pod DNS for proxying from non-head pods
HEAD_HOST = f"{SERVICE_NAME}-0.{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local"


def _get_k8s_token():
    try:
        with open(TOKEN_PATH) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def _k8s_ssl_context():
    return ssl.create_default_context(cafile=CA_PATH)


def _patch_pod_label(label_key, label_value):
    """Patch a label on this pod via K8s API."""
    token = _get_k8s_token()
    if not token:
        return
    hostname = os.environ.get("HOSTNAME", "")
    if not hostname:
        return
    url = f"{K8S_API}/api/v1/namespaces/{NAMESPACE}/pods/{hostname}"
    data = json.dumps({"metadata": {"labels": {label_key: label_value}}}).encode()
    req = urllib.request.Request(url, data=data, method="PATCH")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/strategic-merge-patch+json")
    try:
        urllib.request.urlopen(req, context=_k8s_ssl_context(), timeout=5)
    except Exception as e:
        import sys
        print(f'WARNING: failed to patch label {label_key}={label_value} '
              f'on {hostname}: {e}', file=sys.stderr)


def _query_pods_for_service(svc_name):
    """Query K8s Pods API for pods matching a service label."""
    token = _get_k8s_token()
    if not token or not svc_name:
        return []

    label = f"skypilot-managed-job-name={svc_name}"
    url = f"{K8S_API}/api/v1/namespaces/{NAMESPACE}/pods?labelSelector={label}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, context=_k8s_ssl_context(), timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        import sys
        print(f'WARNING: failed to query pods for service {svc_name}: {e}',
              file=sys.stderr)
        return []

    nodes = []
    for pod in data.get("items", []):
        ip = pod.get("status", {}).get("podIP", "")
        name = pod.get("metadata", {}).get("name", "")
        labels = pod.get("metadata", {}).get("labels", {})
        rank = int(labels.get("skypilot-node-index", "-1"))

        # Status: prefer skypilot-node-phase label, fall back to K8s state
        phase_label = labels.get("skypilot-node-phase", "")
        if phase_label:
            status = phase_label
        else:
            # Derive from K8s container state
            status = _status_from_container_state(pod)

        # App status from label
        app_status = labels.get("skypilot-app-status") or None

        nodes.append({
            "rank": rank,
            "ip": ip,
            "name": name,
            "status": status,
            "app_status": app_status,
        })
    nodes.sort(key=lambda n: n["rank"])
    return nodes


def _status_from_container_state(pod):
    """Derive SkyPilot status from K8s pod/container state."""
    phase = pod.get("status", {}).get("phase", "Unknown")
    for cs in pod.get("status", {}).get("containerStatuses", []):
        # Check the main container (first one, or named "main")
        cname = cs.get("name", "")
        if cname in ("main", "ray-node", ""):
            state = cs.get("state", {})
            if "terminated" in state:
                exit_code = state["terminated"].get("exitCode", -1)
                return "SUCCEEDED" if exit_code == 0 else "FAILED"
            if "running" in state:
                return "RUNNING"
            if "waiting" in state:
                return "PENDING"
            break
    # Fall back to pod phase
    if phase == "Running":
        return "RUNNING"
    if phase == "Succeeded":
        return "SUCCEEDED"
    if phase == "Failed":
        return "FAILED"
    return "PENDING"


def _build_nodes_response():
    """Build the /nodes response for the head pod.

    Single task:
      {"nodes": [{"rank": 0, "ip": "...", "status": "RUNNING", ...}, ...]}

    Job group:
      {"controller": [{"rank": 0, ...}], "workers": [{"rank": 0, ...}, ...]}
    """
    if JOBGROUP_TASKS:
        # Job group mode: task_name -> node list
        result = {}
        for entry in JOBGROUP_TASKS.split(","):
            entry = entry.strip()
            if ":" not in entry:
                continue
            task_name, svc_name = entry.split(":", 1)
            result[task_name] = _query_pods_for_service(svc_name)
        return result
    else:
        # Single task mode
        return {"nodes": _query_pods_for_service(SERVICE_NAME)}


def _read_app_status_file():
    """Read the app status file."""
    try:
        with open(APP_STATUS_FILE) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def _proxy_to_head():
    """Proxy /nodes request to the head pod."""
    url = f"http://{HEAD_HOST}:{PORT}/nodes"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read()
    except Exception as e:
        return json.dumps({"error": f"proxy failed: {e}"}).encode()


def _app_status_watcher():
    """Background thread: watch app_status file, patch pod label."""
    last_value = None
    while True:
        try:
            current = _read_app_status_file()
            if current and current != last_value:
                _patch_pod_label("skypilot-app-status", current)
                last_value = current
        except Exception as e:
            import sys
            print(f'WARNING: app status watcher error: {e}', file=sys.stderr)
        time.sleep(2)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/nodes" or self.path.startswith("/nodes?"):
            if IS_HEAD:
                body = json.dumps(_build_nodes_response(), indent=2)
                raw = body.encode()
            else:
                raw = _proxy_to_head()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

        elif self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress access logs


if __name__ == "__main__":
    # Start app_status file watcher
    t = threading.Thread(target=_app_status_watcher, daemon=True)
    t.start()

    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
'''

import base64 as _base64

DISCOVERY_SERVER_SCRIPT_B64 = _base64.b64encode(
    DISCOVERY_SERVER_SCRIPT.encode()).decode()
