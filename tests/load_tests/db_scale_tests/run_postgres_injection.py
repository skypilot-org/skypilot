#!/usr/bin/env python3
"""
Runner script to execute PostgreSQL scale test injection from within the pod.
This script copies injection scripts to the pod and executes them.
Injects both clusters/cluster_history and managed jobs.
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

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

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
            f"Copying {dest_filename} to pod"):
        return False

    # Verify the script was copied correctly
    if not run_command(
            f"kubectl exec -n {namespace} {pod_name} -- ls -lh /tmp/{dest_filename}",
            f"Verifying {dest_filename} was copied"):
        return False

    return True


def main():
    """Main function to run the scale test."""
    parser = argparse.ArgumentParser(
        description=
        'Run PostgreSQL scale test injection (clusters + managed jobs) in a Kubernetes pod'
    )
    parser.add_argument('--pod',
                        type=str,
                        required=True,
                        help='Pod name (required)')
    parser.add_argument('--namespace',
                        '-n',
                        type=str,
                        default='skypilot',
                        help='Namespace (default: skypilot)')

    args = parser.parse_args()
    pod_name = args.pod
    namespace = args.namespace
    script_dir = os.path.dirname(os.path.abspath(__file__))

    print("=" * 60)
    print("PostgreSQL Scale Test Runner")
    print("=" * 60)
    print(f"Target pod: {pod_name}")
    print(f"Namespace: {namespace}")
    print("=" * 60)

    # Step 1: Check if psycopg2 is installed in the pod
    if not run_command(
            f"kubectl exec -n {namespace} {pod_name} -- python3 -c 'import psycopg2'",
            "Checking if psycopg2 is installed in pod"):
        print("\nInstalling psycopg2-binary in pod...")
        if not run_command(
                f"kubectl exec -n {namespace} {pod_name} -- pip install psycopg2-binary",
                "Installing psycopg2-binary"):
            print("Failed to install psycopg2-binary. Exiting.")
            return 1

    # Step 2: Copy the injection scripts to the pod
    clusters_script = os.path.join(script_dir, "inject_postgres_clusters.py")
    jobs_script = os.path.join(script_dir, "inject_postgres_managed_jobs.py")

    if not copy_script_to_pod(clusters_script, pod_name, namespace,
                              "inject_postgres_clusters.py"):
        print("Failed to copy clusters injection script. Exiting.")
        return 1

    if not copy_script_to_pod(jobs_script, pod_name, namespace,
                              "inject_postgres_managed_jobs.py"):
        print("Failed to copy managed jobs injection script. Exiting.")
        return 1

    # Step 3: Run the clusters injection script in the pod
    if not run_command(
            f"kubectl exec -n {namespace} {pod_name} -- python3 /tmp/inject_postgres_clusters.py",
            "Running clusters injection script in pod"):
        print("Failed to run clusters injection script. Exiting.")
        return 1

    # Step 4: Run the managed jobs injection script in the pod
    if not run_command(
            f"kubectl exec -n {namespace} {pod_name} -- python3 /tmp/inject_postgres_managed_jobs.py",
            "Running managed jobs injection script in pod"):
        print("Failed to run managed jobs injection script. Exiting.")
        return 1

    print("\n" + "=" * 60)
    print("SUCCESS!")
    print("=" * 60)
    print("Scale test injection completed successfully!")
    print("\nTo clean up the test data, run:")
    print(
        f"  python {os.path.join(script_dir, 'run_postgres_cleanup.py')} --pod {pod_name} -n {namespace}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
