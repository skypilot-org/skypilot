#!/usr/bin/env python3
"""
Runner script to execute PostgreSQL scale test cleanup from within the pod.
This script copies cleanup scripts to the pod and executes them.
Cleans up both clusters/cluster_history and managed jobs.
"""

import argparse
import os
import subprocess
import sys


def run_command(cmd, description):
    """Run a shell command and print the output."""
    print(f"\n{'='*60}")
    print(f"{description}")
    print(f"{'='*60}")
    print(f"Running: {cmd}")

    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True
    )

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr, file=sys.stderr)

    if result.returncode != 0:
        print(f"Error: Command failed with exit code {result.returncode}")
        return False

    return True


def copy_script_to_pod(script_path, pod_name, namespace, dest_filename):
    """Copy a script to the pod using stdin."""
    if not run_command(
        f"cat {script_path} | kubectl exec -i -n {namespace} {pod_name} -- sh -c 'cat > /tmp/{dest_filename}'",
        f"Copying {dest_filename} to pod"
    ):
        return False

    # Verify the script was copied correctly
    if not run_command(
        f"kubectl exec -n {namespace} {pod_name} -- ls -lh /tmp/{dest_filename}",
        f"Verifying {dest_filename} was copied"
    ):
        return False

    return True


def main():
    """Main function to run the cleanup."""
    parser = argparse.ArgumentParser(
        description='Run PostgreSQL scale test cleanup (clusters + managed jobs) in a Kubernetes pod'
    )
    parser.add_argument(
        '--pod',
        type=str,
        required=True,
        help='Pod name (required)'
    )
    parser.add_argument(
        '--namespace',
        '-n',
        type=str,
        default='skypilot',
        help='Namespace (default: skypilot)'
    )

    args = parser.parse_args()
    pod_name = args.pod
    namespace = args.namespace
    script_dir = os.path.dirname(os.path.abspath(__file__))

    print("=" * 60)
    print("PostgreSQL Cleanup Runner")
    print("=" * 60)
    print(f"Target pod: {pod_name}")
    print(f"Namespace: {namespace}")
    print("=" * 60)

    # Step 1: Copy the cleanup scripts to the pod
    clusters_script = os.path.join(script_dir, "cleanup_postgres_clusters.py")
    jobs_script = os.path.join(script_dir, "cleanup_postgres_managed_jobs.py")

    if not copy_script_to_pod(clusters_script, pod_name, namespace, "cleanup_postgres_clusters.py"):
        print("Failed to copy clusters cleanup script. Exiting.")
        return 1

    if not copy_script_to_pod(jobs_script, pod_name, namespace, "cleanup_postgres_managed_jobs.py"):
        print("Failed to copy managed jobs cleanup script. Exiting.")
        return 1

    # Step 2: Run the clusters cleanup script in the pod
    if not run_command(
        f"kubectl exec -n {namespace} {pod_name} -- python3 /tmp/cleanup_postgres_clusters.py",
        "Running clusters cleanup script in pod"
    ):
        print("Failed to run clusters cleanup script. Exiting.")
        return 1

    # Step 3: Run the managed jobs cleanup script in the pod
    if not run_command(
        f"kubectl exec -n {namespace} {pod_name} -- python3 /tmp/cleanup_postgres_managed_jobs.py",
        "Running managed jobs cleanup script in pod"
    ):
        print("Failed to run managed jobs cleanup script. Exiting.")
        return 1

    print("\n" + "=" * 60)
    print("SUCCESS!")
    print("=" * 60)
    print("Cleanup completed successfully!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
