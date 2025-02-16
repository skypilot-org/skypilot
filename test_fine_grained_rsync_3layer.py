"""
test_fine_grained_rsync_3layer.py

Compares different ways to download logs in a 3-layer architecture:
1) Replicas -> Controller
2) Controller -> Laptop

We test:
(A) single 'rsync' of entire directory,
(B) file-by-file rsync in series,
(C) file-by-file rsync in parallel (multi-thread).

We measure the total time from when we decide to fetch logs from replicas until
logs arrive at the laptop. The high-level steps:

1) For each replica (replica_i), create a set of large log files (simulate).
2) Download them to the controller either as:
   - one big directory,
   - or file-by-file, possibly in parallel.
3) Then from the controller to the laptop do either a single directory rsync or
   similarly fine-grained approach.

We'll measure the total end-to-end time for each approach and compare.
Finally, we can optionally draw a bar chart or line chart.
"""

import os
import time
import subprocess
import math
from concurrent.futures import ThreadPoolExecutor
import matplotlib.pyplot as plt

CONTROLLER_HOST = "sky-serve-controller-e2dc6f0f"
CONTROLLER_USER = "sky"

# Suppose we have two replicas:
REPLICA_HOSTS = ["10.0.0.101", "10.0.0.102"]
REPLICA_USER = "sky"

def ssh_run(cmd_list, check=True, capture_output=False):
    """Helper to run a local subprocess command, e.g. ssh ..."""
    print(f"[CMD] {' '.join(cmd_list)}")
    return subprocess.run(cmd_list, check=check, capture_output=capture_output, text=True)

def generate_logs_on_replica(replica_ip, file_count, file_size_mb, remote_replica_dir):
    """
    Use ssh to connect from laptop -> controller -> replica to generate logs.
    The logs are created under `remote_replica_dir` on the replica.
    """
    # On the controller, we run an ssh to the replica
    # This will create random logs of specified size
    # e.g. dd if=/dev/urandom of=... bs=1M count=file_size_mb
    # Repeat file_count times
    init_cmd = f"mkdir -p {remote_replica_dir} && rm -f {remote_replica_dir}/log_*"
    ssh_run([
        "ssh", f"{CONTROLLER_USER}@{CONTROLLER_HOST}",
        f"ssh {REPLICA_USER}@{replica_ip} '{init_cmd}'"
    ])
    for i in range(file_count):
        file_cmd = (
            f"ssh {REPLICA_USER}@{replica_ip} "
            f"'dd if=/dev/urandom of={remote_replica_dir}/log_{i}.bin bs=1M count={file_size_mb}'"
        )
        ssh_run(["ssh", f"{CONTROLLER_USER}@{CONTROLLER_HOST}", file_cmd])

def approach_replica_to_controller_single_dir(replica_ip, remote_replica_dir, remote_controller_dir):
    """
    (A) Transfer entire directory from replica -> controller in one rsync
    """
    # On the controller:
    #   rsync -avz sky@replica_ip:remote_replica_dir/ remote_controller_dir
    cmd = [
        "ssh", f"{CONTROLLER_USER}@{CONTROLLER_HOST}",
        "rsync", "-avz",
        f"{REPLICA_USER}@{replica_ip}:{remote_replica_dir}/",
        f"{remote_controller_dir}/"
    ]
    ssh_run(cmd)

def approach_replica_to_controller_file_by_file(replica_ip, remote_replica_dir, remote_controller_dir, parallel=False, threads=4):
    """
    (B) or (C) Transfer file-by-file. If parallel=True, do in threads.
    We'll first list files, then for each file do an rsync.
    """
    # List files
    ls_cmd = [
        "ssh", f"{CONTROLLER_USER}@{CONTROLLER_HOST}",
        "ssh", f"{REPLICA_USER}@{replica_ip}",
        f"ls {remote_replica_dir}"
    ]
    result = ssh_run(ls_cmd, capture_output=True)
    files = result.stdout.strip().split()

    def rsync_one_file(fname):
        cmd = [
            "ssh", f"{CONTROLLER_USER}@{CONTROLLER_HOST}",
            "rsync", "-avz",
            f"{REPLICA_USER}@{replica_ip}:{remote_replica_dir}/{fname}",
            f"{remote_controller_dir}/{fname}"
        ]
        ssh_run(cmd)

    if parallel:
        with ThreadPoolExecutor(max_workers=threads) as exe:
            exe.map(rsync_one_file, files)
    else:
        for f in files:
            rsync_one_file(f)

def controller_to_laptop_rsync_all(remote_controller_dir, local_dir):
    """
    Single directory-level rsync from controller -> laptop
    """
    if not os.path.exists(local_dir):
        os.makedirs(local_dir, exist_ok=True)
    cmd = [
        "rsync", "-avz",
        f"{CONTROLLER_USER}@{CONTROLLER_HOST}:{remote_controller_dir}/",
        f"{local_dir}/"
    ]
    ssh_run(cmd)

def run_experiment():
    # We'll compare:
    #  1) replica->controller single dir + controller->laptop single dir
    #  2) replica->controller file-by-file + controller->laptop single dir
    #  3) replica->controller parallel file-by-file + controller->laptop single dir
    # replicate for different file_count and file_size
    file_sizes_mb = [10, 50]
    file_counts = [5, 20]
    base_replica_dir = "/home/sky/replica_logs"
    base_controller_dir = "/home/sky/consolidated_logs"
    local_out = "./local_download"

    results = []

    for fs in file_sizes_mb:
        for fc in file_counts:
            # Clear local
            if os.path.exists(local_out):
                for item in os.listdir(local_out):
                    os.remove(os.path.join(local_out, item))
            # Create logs on each replica
            for rip in REPLICA_HOSTS:
                generate_logs_on_replica(rip, fc, fs, base_replica_dir)

            # Approach 1: single dir from each replica, then single dir to laptop
            # Clear the controller folder
            cmd_clear = [
                "ssh", f"{CONTROLLER_USER}@{CONTROLLER_HOST}",
                f"rm -rf {base_controller_dir} && mkdir -p {base_controller_dir}"
            ]
            ssh_run(cmd_clear)

            t0 = time.time()
            # replica->controller
            for rip in REPLICA_HOSTS:
                approach_replica_to_controller_single_dir(rip, base_replica_dir, base_controller_dir)
            # controller->laptop
            controller_to_laptop_rsync_all(base_controller_dir, local_out)
            t1 = time.time() - t0

            # Approach 2: file-by-file from each replica, then single dir to laptop
            ssh_run(cmd_clear)
            t2_start = time.time()
            for rip in REPLICA_HOSTS:
                approach_replica_to_controller_file_by_file(rip, base_replica_dir, base_controller_dir, parallel=False)
            controller_to_laptop_rsync_all(base_controller_dir, local_out)
            t2 = time.time() - t2_start

            # Approach 3: parallel file-by-file
            ssh_run(cmd_clear)
            t3_start = time.time()
            for rip in REPLICA_HOSTS:
                approach_replica_to_controller_file_by_file(rip, base_replica_dir, base_controller_dir, parallel=True, threads=4)
            controller_to_laptop_rsync_all(base_controller_dir, local_out)
            t3 = time.time() - t3_start

            results.append((fs, fc, t1, t2, t3))
            print(f"[Summary] fileSize={fs}MB, fileCount={fc}: singleDir={t1:.2f}s, fileByFile={t2:.2f}s, parallel={t3:.2f}s")

    # Potentially plot
    # x-axis: (fs, fc), y-axis: times
    # example
    xlabels = []
    times_dir = []
    times_file = []
    times_parallel = []
    for (fs, fc, td, tf, tp) in results:
        xlabels.append(f"{fs}MB-{fc}")
        times_dir.append(td)
        times_file.append(tf)
        times_parallel.append(tp)

    plt.figure(figsize=(10, 5))
    X = range(len(xlabels))
    plt.bar([x+0.0 for x in X], times_dir, width=0.25, label="single-dir")
    plt.bar([x+0.25 for x in X], times_file, width=0.25, label="file-by-file")
    plt.bar([x+0.50 for x in X], times_parallel, width=0.25, label="parallel-file")
    plt.xticks([x+0.25 for x in X], xlabels)
    plt.ylabel("Time (s)")
    plt.title("Replica->Controller->Laptop: Rsync Approaches")
    plt.legend()
    plt.savefig("3layer_rsync_compare.png")

if __name__ == "__main__":
    run_experiment()