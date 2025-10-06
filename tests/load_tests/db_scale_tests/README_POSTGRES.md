# PostgreSQL Scale Testing

This directory contains scripts to inject mock data into a PostgreSQL database for performance and scale testing.

## Overview

The scripts adapt the existing SQLite-based test infrastructure to work with PostgreSQL by:
- Replacing `sqlite3` calls with `psycopg2`
- Running scripts from within a Kubernetes pod where PostgreSQL is accessible
- Reusing the existing `SampleBasedGenerator` logic to create realistic test data

## Files

### Core Scripts (run in pod)
- **`inject_postgres_clusters.py`** - Injects 2,000 active clusters + 10,000 cluster history entries into PostgreSQL
- **`inject_postgres_managed_jobs.py`** - Injects 10,000 test managed jobs into PostgreSQL
- **`cleanup_postgres_clusters.py`** - Removes test clusters and cluster history from PostgreSQL
- **`cleanup_postgres_managed_jobs.py`** - Removes test managed jobs from PostgreSQL

### Runner Scripts (run locally)
- **`run_postgres_injection.py`** - Copies and runs both injection scripts in the pod (clusters + managed jobs)
- **`run_postgres_cleanup.py`** - Copies and runs both cleanup scripts in the pod (clusters + managed jobs)

### Existing SQLite Scripts
- **`inject_test_managed_jobs.py`** - Injects test jobs into SQLite
- **`cleanup_test_managed_jobs.py`** - Removes test jobs from SQLite
- **`scale_test_utils.py`** - Contains TestScale class with injection logic
- **`sample_based_generator.py`** - Contains SampleBasedGenerator for creating realistic test data
- **`run_scale_test.py`** - Comprehensive scale test runner for SQLite

## Prerequisites

### In the Kubernetes Pod
- Python 3 with `psycopg2` or `psycopg2-binary` installed
- PostgreSQL database accessible at `localhost:5432`
- Database credentials: `skypilot/skypilot@skypilot`
- Sample clusters must exist:
  - `scale-test-active` - An active cluster to use as template
  - `scale-test-terminated` - A terminated cluster in cluster_history to use as template
- A sample managed job (job_id=9) must exist in the database

### Local Environment
- `kubectl` configured with access to the cluster
- Pod name (must be provided as CLI argument)
- Namespace (defaults to `skypilot`, can be overridden with `-n` flag)

## Usage

### Quick Start

1. **Inject test data (clusters + managed jobs):**
   ```bash
   cd /Users/rsonecha/Documents/assemble/skypilot/tests/load_tests/db_scale_tests
   python3 run_postgres_injection.py --pod <pod-name>

   # Or with custom namespace:
   python3 run_postgres_injection.py --pod <pod-name> -n <namespace>
   ```

   This will inject:
   - 2,000 active clusters (with cluster_yaml entries)
   - 10,000 cluster history entries (2,000 recent + 8,000 old)
   - 10,000 managed jobs

2. **Verify the injection:**
   ```bash
   kubectl exec -n <namespace> <pod-name> -- python3 -c "
   import psycopg2
   conn = psycopg2.connect(host='localhost', port=5432, database='skypilot', user='skypilot', password='skypilot')
   cursor = conn.cursor()
   cursor.execute('SELECT COUNT(*) FROM clusters')
   print(f'Total clusters: {cursor.fetchone()[0]}')
   cursor.execute('SELECT COUNT(*) FROM cluster_yaml')
   print(f'Total cluster_yaml entries: {cursor.fetchone()[0]}')
   cursor.execute('SELECT COUNT(*) FROM cluster_history')
   print(f'Total cluster history: {cursor.fetchone()[0]}')
   cursor.execute('SELECT COUNT(*) FROM spot')
   print(f'Total jobs: {cursor.fetchone()[0]}')
   cursor.close()
   conn.close()
   "
   ```

3. **Clean up test data:**
   ```bash
   python3 run_postgres_cleanup.py --pod <pod-name>

   # Or with custom namespace:
   python3 run_postgres_cleanup.py --pod <pod-name> -n <namespace>
   ```

### Manual Execution

If you prefer to run the scripts manually in the pod:

1. **Copy the cluster injection script:**
   ```bash
   cat inject_postgres_clusters.py | kubectl exec -i -n <namespace> <pod-name> -- sh -c 'cat > /tmp/inject_postgres_clusters.py'
   ```

2. **Run the cluster injection:**
   ```bash
   kubectl exec -n <namespace> <pod-name> -- python3 /tmp/inject_postgres_clusters.py
   ```

3. **Copy the managed jobs injection script:**
   ```bash
   cat inject_postgres_managed_jobs.py | kubectl exec -i -n <namespace> <pod-name> -- sh -c 'cat > /tmp/inject_postgres_managed_jobs.py'
   ```

4. **Run the managed jobs injection:**
   ```bash
   kubectl exec -n <namespace> <pod-name> -- python3 /tmp/inject_postgres_managed_jobs.py
   ```

5. **Copy the cleanup scripts:**
   ```bash
   cat cleanup_postgres_clusters.py | kubectl exec -i -n <namespace> <pod-name> -- sh -c 'cat > /tmp/cleanup_postgres_clusters.py'
   cat cleanup_postgres_managed_jobs.py | kubectl exec -i -n <namespace> <pod-name> -- sh -c 'cat > /tmp/cleanup_postgres_managed_jobs.py'
   ```

6. **Run the cleanup:**
   ```bash
   kubectl exec -n <namespace> <pod-name> -- python3 /tmp/cleanup_postgres_clusters.py
   kubectl exec -n <namespace> <pod-name> -- python3 /tmp/cleanup_postgres_managed_jobs.py
   ```

## How It Works

### Data Generation

The scripts use a **sample-based approach** to create realistic test data:

#### Clusters
1. **Sample Cluster**: Load an existing active cluster (`scale-test-active`) as a template
2. **Generate Copies**: Create 2,000 copies with:
   - Unique cluster names (format: `test-cluster-NNNN-XXXXXXXX`)
   - Unique `cluster_hash` values (UUIDs)
   - Random timestamps for `launched_at` and `status_updated_at`
   - cluster_yaml entries copied from sample cluster
3. **Insert in Batches**: Insert into both `clusters` and `cluster_yaml` tables in batches of 100

#### Cluster History
1. **Sample Cluster**: Load a terminated cluster (`scale-test-terminated`) as a template
2. **Generate Copies**: Create 10,000 copies (2,000 recent + 8,000 old) with:
   - Unique cluster names (format: `test-cluster-recent-NNNN` or `test-cluster-old-NNNN`)
   - Random timestamps (recent: 1-9 days ago, old: 15-30 days ago)
3. **Insert in Batches**: Insert into `cluster_history` table in batches of 100

#### Managed Jobs
1. **Sample Job**: Load an existing managed job (job_id=9) as a template
2. **Generate Copies**: Create 10,000 copies with:
   - Unique `job_id` values (sequential from max_job_id + 1)
   - Unique timestamps (`submitted_at`, `start_at`, `last_recovered_at`)
   - Unique `run_timestamp` (format: `sky-YYYY-MM-DD-HH-MM-SS-XXXXXX`)
   - Unique `controller_pid` (negative random values)
   - Modified file paths (`dag_yaml_path`, `env_file_path`, `original_user_yaml_path`)
3. **Insert in Batches**: Insert into both `spot` and `job_info` tables in batches of 100

### Database Schema

The scripts work with these tables:

- **`clusters` table**: Active clusters with metadata
- **`cluster_yaml` table**: YAML configuration for each cluster (linked by cluster_name)
- **`cluster_history` table**: Terminated clusters with metadata
- **`spot` table**: Main managed jobs table with job metadata
- **`job_info` table**: Additional job information linked by `spot_job_id`

### Cleanup Logic

The cleanup scripts identify test data by:
- **Clusters**: Names starting with `'test-cluster-'`
- **Cluster history**: Names starting with `'test-cluster-recent-'` or `'test-cluster-old-'`
- **Managed jobs**: `run_timestamp` starts with `'sky-'` and `job_id > 1`

## Configuration

### Customizing Cluster Count

To inject a different number of clusters, edit `inject_postgres_clusters.py`:

```python
cluster_count = 2000  # Active clusters
recent_history_count = 2000  # Recent cluster history (1-10 days)
old_history_count = 8000  # Old cluster history (15-30 days)
```

### Customizing Template Clusters

To use different sample clusters, edit `inject_postgres_clusters.py`:

```python
active_cluster_name = 'scale-test-active'  # Active cluster template
terminated_cluster_name = 'scale-test-terminated'  # Terminated cluster template
```

### Customizing Job Count

To inject a different number of jobs, edit `inject_postgres_managed_jobs.py`:

```python
count = 10000  # Change this value
```

### Customizing Template Job

To use a different sample job, edit `inject_postgres_managed_jobs.py`:

```python
managed_job_id = 9  # Change this to your sample job ID
```

### Customizing Database Connection

Edit the `db_params` in both injection and cleanup scripts:

```python
db_params = {
    'host': 'localhost',
    'port': 5432,
    'database': 'skypilot',
    'user': 'skypilot',
    'password': 'skypilot'
}
```

### Customizing Pod and Namespace

Pass the pod name and namespace as command-line arguments:

```bash
python3 run_postgres_injection.py --pod my-custom-pod -n my-namespace
python3 run_postgres_cleanup.py --pod my-custom-pod -n my-namespace
```

The `--pod` argument is required. The `-n/--namespace` argument is optional and defaults to `skypilot`.

## Performance

The injection scripts process data in batches of 100 for optimal performance:

- **Batch size**: 100 items per batch
- **Expected time**:
  - Clusters: ~15-30 seconds for 2,000 clusters
  - Cluster history: ~60-90 seconds for 10,000 entries
  - Managed jobs: ~30-60 seconds for 10,000 jobs
- **Progress output**:
  - Clusters: Every 500 clusters
  - Cluster history: Every 1,000 entries
  - Jobs: Every 1,000 jobs

## Troubleshooting

### psycopg2 Not Installed

If you get an import error for psycopg2:

```bash
kubectl exec -n <namespace> <pod-name> -- pip install psycopg2-binary
```

### Connection Failed

Verify PostgreSQL is accessible:

```bash
kubectl exec -n <namespace> <pod-name> -- python3 -c "import psycopg2; conn = psycopg2.connect(host='localhost', port=5432, database='skypilot', user='skypilot', password='skypilot'); print('Connected!'); conn.close()"
```

### Sample Clusters Not Found

Create sample clusters first:

```bash
# Create an active cluster
sky launch --infra k8s -c scale-test-active -y "echo 'active cluster'"

# Create and terminate a cluster for history
sky launch --infra k8s -c scale-test-terminated -y "echo 'terminated cluster'"
sky down scale-test-terminated -y
```

### Sample Job Not Found

Create a sample managed job first:

```bash
sky jobs launch --infra k8s "sleep 10000000"
```

Then find its job_id and update the script.

### kubectl cp Permission Errors

If `kubectl cp` fails with permission errors, the runner scripts use stdin redirection as a workaround:

```bash
cat script.py | kubectl exec -i -n namespace pod -- sh -c 'cat > /tmp/script.py'
```

## Results

After running the injection script, you should see output for both clusters and jobs:

```
============================================================
INJECTION COMPLETE!
============================================================
Total active clusters injected: 2000
Total cluster history injected: 10000
  - Recent (1-10 days): 2000
  - Old (15-30 days): 8000

To clean up these clusters, run:
  python cleanup_postgres_clusters.py

============================================================
INJECTION COMPLETE!
============================================================
Total jobs injected: 10000

To clean up these jobs, run:
  python cleanup_postgres_managed_jobs.py
```

Verify with:

```bash
kubectl exec -n <namespace> <pod-name> -- python3 -c "
import psycopg2
conn = psycopg2.connect(host='localhost', port=5432, database='skypilot', user='skypilot', password='skypilot')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM clusters')
print(f'Total clusters: {cursor.fetchone()[0]}')
cursor.execute('SELECT COUNT(*) FROM cluster_yaml')
print(f'Total cluster_yaml: {cursor.fetchone()[0]}')
cursor.execute('SELECT COUNT(*) FROM cluster_history')
print(f'Total cluster history: {cursor.fetchone()[0]}')
cursor.execute('SELECT COUNT(*) FROM spot')
print(f'Total jobs: {cursor.fetchone()[0]}')
cursor.close()
conn.close()
"
```

Expected output:
```
Total clusters: 2001 (2000 test + 1 sample)
Total cluster_yaml: 2001
Total cluster history: 10001 (10000 test + 1 sample)
Total jobs: 10001 (10000 test + 1 sample)
```
