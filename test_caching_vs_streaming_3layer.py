"""
test_caching_vs_streaming_3layer.py

Evaluates trade-offs of disk caching on the controller vs. direct streaming from replicas each time.
We measure how repeated downloads are affected by caching, plus the disk usage overhead on the controller.

Scenario:
1. Each replica has large logs (we create them).
2. If "caching" is ON, the controller might keep the logs locally, so the second or third sync from laptop is faster.
   If "caching" is OFF, each time we do `replica->controller->laptop`.
3. We measure total time for the first and subsequent downloads, as well as controller disk usage.
"""

import os
import time
import subprocess
import math

CONTROLLER_HOST = "sky-serve-controller-e2dc6f0f"
CONTROLLER_USER = "sky"
REPLICA_HOSTS = ["10.0.0.101", "10.0.0.102"]
REPLICA_USER = "sky"

def ssh_run(cmd_list, check=True, capture_output=False):
    print(f"[CMD] {' '.join(cmd_list)}")
    return subprocess.run(cmd_list, check=check, capture_output=capture_output, text=True)

def enable_controller_caching(enable=True):
    """
    Placeholder: you might create a file or set an env variable on controller
    to toggle "caching" mode. For real usage, you'd adjust your actual logic.
    """
    mode = "true" if enable else "false"
    ssh_run([
        "ssh", f"{CONTROLLER_USER}@{CONTROLLER_HOST}",
        f"echo {mode} > /tmp/ENABLE_SERVE_CACHE"
    ])

def generate_logs_on_replica(replica_ip, file_size_mb, file_count, replica_dir):
    """
    same as in previous script
    """
    init_cmd = f"mkdir -p {replica_dir} && rm -f {replica_dir}/log_*"
    ssh_run([
        "ssh", f"{CONTROLLER_USER}@{CONTROLLER_HOST}",
        f"ssh {REPLICA_USER}@{replica_ip} '{init_cmd}'"
    ])
    for i in range(file_count):
        file_cmd = (
            f"ssh {REPLICA_USER}@{replica_ip} "
            f"'dd if=/dev/urandom of={replica_dir}/log_{i}.bin bs=1M count={file_size_mb}'"
        )
        ssh_run(["ssh", f"{CONTROLLER_USER}@{CONTROLLER_HOST}", file_cmd])

def measure_download_laptop(service_name="sky-service-6c01"):
    """
    Simulate one call to "sky serve logs --sync-down" or
    do your real commands (two-tier approach).
    Return elapsed time in seconds.
    """
    start = time.time()
    cmd = ["sky", "serve", "logs", service_name, "--sync-down"]
    ssh_run(cmd)
    return time.time() - start

def get_controller_disk_usage(path="/home/sky/.sky/serve"):
    # example usage
    cmd = [
        "ssh", f"{CONTROLLER_USER}@{CONTROLLER_HOST}",
        f"du -sh {path}"
    ]
    result = ssh_run(cmd, capture_output=True)
    return result.stdout.strip()

def run_caching_experiment():
    base_replica_dir = "/home/sky/replica_logs"
    file_sizes = [50, 200]  # MB
    file_counts = [2, 10]

    results = []

    for fs in file_sizes:
        for fc in file_counts:
            # generate logs on each replica
            for rip in REPLICA_HOSTS:
                generate_logs_on_replica(rip, fs, fc, base_replica_dir)

            # Turn caching on
            enable_controller_caching(True)
            t1 = measure_download_laptop()
            # do a second download (simulate repeated request)
            t2 = measure_download_laptop()
            usage1 = get_controller_disk_usage("/home/sky/.sky/serve")

            # Turn caching off
            enable_controller_caching(False)
            t3 = measure_download_laptop()
            t4 = measure_download_laptop()
            usage2 = get_controller_disk_usage("/home/sky/.sky/serve")

            print(f"[cache=on first/second] {t1:.2f}s / {t2:.2f}s, disk={usage1}")
            print(f"[cache=off first/second] {t3:.2f}s / {t4:.2f}s, disk={usage2}")

            results.append({
                "filesize_mb": fs,
                "filecount": fc,
                "time_cache_first": t1,
                "time_cache_second": t2,
                "disk_usage_on": usage1,
                "time_nocache_first": t3,
                "time_nocache_second": t4,
                "disk_usage_off": usage2
            })

    # You can optionally parse usage strings, store numeric data, plot them
    # or just print final results:
    for r in results:
        print(r)

if __name__ == "__main__":
    run_caching_experiment()

# Use SKYPILOT_DEV=1 to run on a dev cluster.
"""
(skypilot-runtime) (base) sky@sky-serve-controller-e2dc6f0f-e2dc6f0f-head:~$ sky status sky-service-6c01-1 --endpoints
8080: 34.27.182.15:8080
(skypilot-runtime) (base) sky@sky-serve-controller-e2dc6f0f-e2dc6f0f-head:~$ sky status sky-service-6c01-2 --endpoints
8080: 34.69.232.244:8080
"""