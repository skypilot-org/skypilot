# PostgreSQL Scale Testing

This guide covers running scale tests against a PostgreSQL database (typically in a Kubernetes environment). For general information about scale testing, see [README.md](README.md).

## Overview

The PostgreSQL scripts adapt the SQLite-based test infrastructure to work with PostgreSQL by:
- Replacing `sqlite3` calls with `psycopg2`
- Running scripts from within a Kubernetes pod where PostgreSQL is accessible
- Using the same sample-based data generation approach

## Files

### Core Scripts (run in pod)
- **`inject_postgres_clusters.py`** - Injects clusters and cluster history
- **`inject_postgres_managed_jobs.py`** - Injects managed jobs
- **`cleanup_postgres_clusters.py`** - Removes test clusters
- **`cleanup_postgres_managed_jobs.py`** - Removes test jobs

### Runner Scripts (run locally)
- **`run_postgres_injection.py`** - Copies and runs both injection scripts in the pod
- **`run_postgres_cleanup.py`** - Copies and runs both cleanup scripts in the pod

## Prerequisites

### Sample Data Setup
See [README.md](README.md#required-sample-data-setup) for instructions on creating the required sample clusters and jobs.

### Kubernetes Environment
- `kubectl` configured with access to the cluster
- Python 3 with `psycopg2-binary` in the target pod (auto-installed by runner scripts)
- PostgreSQL database accessible from the pod

## Usage

### Quick Start

1. **Inject test data:**
   ```bash
   python3 run_postgres_injection.py --pod <pod-name> -n <namespace>
   ```

   This injects the default test data (see [README.md](README.md) for scale details).

2. **Clean up test data:**
   ```bash
   python3 run_postgres_cleanup.py --pod <pod-name> -n <namespace>
   ```

### Customizing the Injection

The injection scripts support various CLI arguments. To customize, run them manually in the pod:

```bash
# Example: Inject 5000 clusters with custom template
kubectl exec -n <namespace> <pod-name> -- python3 /tmp/inject_postgres_clusters.py \
  --cluster-count 5000 \
  --recent-history 3000 \
  --old-history 12000 \
  --active-cluster my-custom-active \
  --terminated-cluster my-custom-terminated

# Example: Inject 20000 jobs from job ID 5
kubectl exec -n <namespace> <pod-name> -- python3 /tmp/inject_postgres_managed_jobs.py \
  --count 20000 \
  --job-id 5
```

For all available options, run with `--help`:
```bash
kubectl exec -n <namespace> <pod-name> -- python3 /tmp/inject_postgres_clusters.py --help
kubectl exec -n <namespace> <pod-name> -- python3 /tmp/inject_postgres_managed_jobs.py --help
```

## How It Works

See [README.md](README.md#how-it-works) for details on the data generation approach. The PostgreSQL scripts use the same logic with `psycopg2` instead of `sqlite3`.

## Troubleshooting

### psycopg2 Not Installed

The runner scripts (`run_postgres_injection.py`) auto-install `psycopg2-binary` if needed. To install manually:

```bash
kubectl exec -n <namespace> <pod-name> -- pip install psycopg2-binary
```

### Sample Data Not Found

See [README.md](README.md#required-sample-data-setup) for instructions on creating sample clusters and jobs.

### Database Connection Issues

Default connection assumes:
- Host: `localhost`
- Port: `5432`
- Database: `skypilot`
- User/Password: `skypilot/skypilot`

To use different credentials, pass arguments to the injection scripts:
```bash
kubectl exec -n <namespace> <pod-name> -- python3 /tmp/inject_postgres_clusters.py \
  --host my-db-host \
  --port 5433 \
  --database my-db \
  --user my-user \
  --password my-pass
```
