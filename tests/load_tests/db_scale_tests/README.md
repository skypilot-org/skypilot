# Scale Testing Tools

This folder contains tools for testing SkyPilot operations at large scale. These tests are designed to evaluate performance and behavior with significant amounts of data:

- **2,000 active clusters** in the clusters table
- **10,000 terminated clusters** in the cluster_history table (2,000 recent + 8,000 older)
- **10,000 managed jobs** in the spot and job_info tables

The scale tests help understand performance bottlenecks and database query performance under realistic enterprise scale.

## Important Notes

 **Local Testing Warning**: The automated tests in this folder are currently set up to run locally with a SQLite backend. This means they will modify your local SkyPilot databases:

- `~/.sky/state.db` (clusters and cluster_history tables)
- `~/.sky/spot_jobs.db` (spot and job_info tables)

The tests will add many entries to these tables during execution but will clean up automatically at the end.

 **PostgreSQL Testing**: The `test_large_production_performance.sh` script supports testing with AWS RDS PostgreSQL database. When using the `--postgres` flag, the script will:
- Create an AWS RDS PostgreSQL instance (named `skypilot-large-production-test-db`)
- Configure the SkyPilot API server to use the PostgreSQL database
- Automatically restart the API server with the database connection
- Clean up the RDS instance on test completion

**Prerequisites for PostgreSQL Testing:**
- AWS CLI installed and configured with appropriate credentials
- AWS account with permissions to create/delete RDS instances
- Default VPC with at least 2 subnets in different availability zones

## Prerequisites

Before running scale tests, you **must**:

1. **Enable consolidation mode**: The server must be running under consolidation mode. Ensure your `~/.sky/config.yaml` has:
   ```yaml
   jobs:
     controller:
       consolidation_mode: true
   ```

2. **Create sample entries**: Create sample entries in your local database that will be used as templates for generating test data. This ensures all generated data matches your environment's specific database schema and constraints.

### Required Sample Data Setup

Run the following commands to create the required sample entries:

#### 1. Create a Terminated Cluster
```bash
# Launch and then terminate a cluster
sky launch --infra k8s -c scale-test-terminated -y "echo 'terminated cluster'"
sky down scale-test-terminated -y
```

#### 2. Create a Running Cluster
```bash
# Launch a cluster that will remain running
sky launch --infra k8s -c scale-test-active -y "echo 'active cluster'"
```

#### 3. Create a Managed Job
```bash
# Launch a long-running managed job with consolidation enabled
sky jobs launch --infra k8s "sleep 10000000"
# Note the job ID from the output (e.g., "Managed job ID: 1")
```

After creating these samples, verify they exist:
```bash
# Check clusters
sky status

# Check managed jobs
sky jobs queue
```

## Test Data Injection

The scale tests inject realistic test data by:

1. **Sample-based generation**: Use real entries from your database as templates to generate test data
2. **Data integrity**: Maintain proper foreign key relationships and realistic timestamps (terminated clusters have different activity times for time-based filtering tests)
3. **Environment compatibility**: Clones your actual database entries ensuring compatibility with your specific setup
4. **Batch processing**: Insert data in batches for optimal performance
5. **Automatic cleanup**: Remove all test data after test completion

The tests require you to provide cluster names and job IDs that will be used as templates for data generation.

## Test Categories

### Database Scale Tests
- **Active Clusters**: `get_clusters()` performance with 2,000 active clusters
- **Cluster History**: `get_clusters_from_history()` performance with time-based filtering (10 days vs 30 days)
- **Managed Jobs**: `get_managed_jobs()` performance with 10,000 managed jobs

### API Performance Tests
- **CLI Commands**: `sky status` and `sky jobs queue` performance at scale

## Running Performance Benchmarks

> **Note**: Make sure to backup your local SkyPilot databases before running scale tests if you have important data.

```bash
# Backup your databases (recommended)
cp ~/.sky/state.db ~/.sky/state.db.backup
cp ~/.sky/spot_jobs.db ~/.sky/spot_jobs.db.backup
```

### Performance Benchmark

For a quick performance overview with detailed timing information:

```bash
# Run the standalone benchmark script with your sample data
# Note: Replace '1' with the actual job ID from your 'sky jobs launch' output
python tests/load_tests/db_scale_tests/run_scale_test.py \
  --active-cluster scale-test-active \
  --terminated-cluster scale-test-terminated \
  --managed-job-id 1
```

This will:
- Use your sample entries as templates
- Inject 2,000 active clusters + 10,000 terminated clusters + 10,000 managed jobs
- Benchmark `get_clusters()`, `get_clusters_from_history()`, `get_managed_jobs()`, and `sky jobs queue`
- Show performance metrics (duration, rate) for each operation
- Automatically clean up test data

#### Configurable Options

The `run_scale_test.py` script supports several command-line arguments to customize your test:

**Sample Data Configuration:**
- `--active-cluster CLUSTER_NAME` - Name of active cluster to use as template (default: `scale-test-active`)
- `--terminated-cluster CLUSTER_NAME` - Name of terminated cluster to use as template (default: `scale-test-terminated`)
- `--managed-job-id JOB_ID` - Job ID of managed job to use as template (default: `1`)

**Test Selection:**
- `--test {all,clusters,history,jobs}` - Which test to run (default: `all`)
  - `all` - Run all tests (clusters + history + jobs)
  - `clusters` - Only test active clusters
  - `history` - Only test cluster history
  - `jobs` - Only test managed jobs

**Dataset Sizes:**
- `--cluster-count N` - Number of active clusters to inject (default: `2000`)
- `--history-recent N` - Number of recent terminated clusters to inject (default: `2000`)
- `--history-old N` - Number of old terminated clusters to inject (default: `8000`)
- `--job-count N` - Number of managed jobs to inject (default: `10000`)

**Example Usage:**

```bash
# Test only active clusters with a smaller dataset
python tests/load_tests/db_scale_tests/run_scale_test.py \
  --test clusters \
  --cluster-count 500

# Test cluster history with custom template and larger dataset
python tests/load_tests/db_scale_tests/run_scale_test.py \
  --test history \
  --terminated-cluster scale-test-terminated \
  --history-recent 5000 \
  --history-old 15000

# Test managed jobs with custom job template
python tests/load_tests/db_scale_tests/run_scale_test.py \
  --test jobs \
  --managed-job-id 5 \
  --job-count 20000
```

## Performance Monitoring

Scale tests are instrumented to measure:

- **Database query execution times** for `get_clusters()`, `get_clusters_from_history()`, and `get_managed_jobs()`
- **API response times** for `sky status` and `sky jobs queue` commands at scale
- **Data injection performance** (batch insert rates)

Results are logged to help identify performance regressions and optimization opportunities.

## Manual Testing with Scaled Database

For manual testing and debugging, you can inject test data without automatic cleanup using helper scripts. This is useful when you want to run `sky status`, `sky jobs queue`, or other commands against a database with scaled test data.

### Inject Test Clusters

```bash
# Inject test clusters (default: 5 clusters)
python tests/load_tests/db_scale_tests/inject_test_clusters.py

# Inject with custom count and template cluster
python tests/load_tests/db_scale_tests/inject_test_clusters.py \
  --count 2000 \
  --active-cluster scale-test-active

# Verify with sky status
sky status

# Clean up when done
python tests/load_tests/db_scale_tests/cleanup_test_clusters.py
```

**Options:**
- `--count N` - Number of test clusters to inject (default: 5)
- `--active-cluster NAME` - Name of active cluster to use as template (default: scale-test-active)

### Inject Test Cluster History

```bash
# Inject test cluster history (default: 5 recent + 5 old)
python tests/load_tests/db_scale_tests/inject_test_cluster_history.py

# Inject with custom counts and template cluster
python tests/load_tests/db_scale_tests/inject_test_cluster_history.py \
  --recent-count 2000 \
  --old-count 8000 \
  --terminated-cluster scale-test-terminated

# Verify programmatically
python -c "from sky import global_user_state; print(len(global_user_state.get_clusters_from_history(days=10)))"

# Clean up when done
python tests/load_tests/db_scale_tests/cleanup_test_cluster_history.py
```

**Options:**
- `--recent-count N` - Number of recent terminated clusters to inject (default: 5)
- `--old-count N` - Number of old terminated clusters to inject (default: 5)
- `--terminated-cluster NAME` - Name of terminated cluster to use as template (default: scale-test-terminated)

### Inject Test Managed Jobs

```bash
# Inject test managed jobs (default: 10 jobs)
python tests/load_tests/db_scale_tests/inject_test_managed_jobs.py

# Inject with custom count and template job
python tests/load_tests/db_scale_tests/inject_test_managed_jobs.py \
  --count 10000 \
  --managed-job-id 2

# Verify with sky jobs queue
sky jobs queue

# Clean up when done (deletes all jobs with ID > 2)
python tests/load_tests/db_scale_tests/cleanup_test_managed_jobs.py --managed-job-id 2
```

**Options:**
- `--count N` - Number of test managed jobs to inject (default: 10)
- `--managed-job-id ID` - Job ID of managed job to use as template (default: 1)

**Cleanup:**
- `--managed-job-id ID` (required) - Deletes all jobs with job_id > this value

**Note**: These scripts inject data but do not clean it up automatically, allowing you to manually test SkyPilot commands against a scaled database. Remember to run the cleanup scripts when you're done testing.

## Production-Scale Data Injection

For testing with production-scale data volumes, use the `inject_production_scale_data.py` script. This script reuses the existing test utilities to inject realistic production-scale data:

- **1,500 active clusters** (using `inject_clusters()`)
- **220,000 history clusters** (using `inject_cluster_history()` - 20% recent, 80% old)
- **290,000 cluster events** associated with clusters
- **12,500 managed jobs** (using `inject_managed_jobs()`)

**Note:** The script uses the same `TestScale` class from `scale_test_utils.py`, so all data inherits GPU types, regions, and other resource information from your sample clusters (created in Prerequisites). This ensures the test data matches your actual environment.

### Usage

```bash
# Inject all production-scale data
python tests/load_tests/db_scale_tests/inject_production_scale_data.py \
  --active-cluster scale-test-active \
  --terminated-cluster scale-test-terminated \
  --managed-job-id 1

# Inject with custom counts
python tests/load_tests/db_scale_tests/inject_production_scale_data.py \
  --active-cluster scale-test-active \
  --terminated-cluster scale-test-terminated \
  --managed-job-id 1 \
  --active-clusters 2000 \
  --history-clusters 300000 \
  --cluster-events 400000 \
  --managed-jobs 15000

# Inject only specific data types
python tests/load_tests/db_scale_tests/inject_production_scale_data.py \
  --active-cluster scale-test-active \
  --terminated-cluster scale-test-terminated \
  --managed-job-id 1 \
  --skip-history \
  --skip-events

# Clean up (undo) all production-scale data
# Note: --managed-job-id is optional. If not provided, managed jobs won't be cleaned up.
python tests/load_tests/db_scale_tests/inject_production_scale_data.py \
  --cleanup

# Clean up including managed jobs (provide the template job ID)
python tests/load_tests/db_scale_tests/inject_production_scale_data.py \
  --cleanup \
  --managed-job-id 2
```

**Options:**
- `--active-cluster NAME` - Name of active cluster template (default: `scale-test-active`)
- `--terminated-cluster NAME` - Name of terminated cluster template (default: `scale-test-terminated`)
- `--managed-job-id ID` - Job ID of managed job template (default: `1`)
- `--active-clusters N` - Number of active clusters (default: `1500`)
- `--history-clusters N` - Number of history clusters (default: `220000`)
- `--cluster-events N` - Number of cluster events (default: `290000`)
- `--managed-jobs N` - Number of managed jobs (default: `12500`)
- `--skip-clusters` - Skip injecting active clusters
- `--skip-history` - Skip injecting cluster history
- `--skip-events` - Skip injecting cluster events
- `--skip-jobs` - Skip injecting managed jobs
- `--cleanup` - Clean up (undo) all production-scale data instead of injecting. `--managed-job-id` is optional; if not provided, managed jobs won't be cleaned up.

**Cleanup/Undo:**
The `--cleanup` flag removes all production-scale data that was injected:
- All clusters with names matching `prod-cluster-*`
- All cluster history entries with names matching `prod-hist-*`
- All cluster events associated with production clusters
- All managed jobs with `job_id > --managed-job-id` (only if `--managed-job-id` is provided)

**Why `--managed-job-id` is needed for job cleanup:**
Unlike clusters which have predictable name patterns (`prod-cluster-*`), managed jobs are identified by their `job_id`. The cleanup script needs to know which job ID was used as the template to delete all jobs created after it. If you don't remember the template job ID, you can skip job cleanup by omitting `--managed-job-id` - clusters, history, and events will still be cleaned up.

**Note**: This script injects large amounts of data and may take significant time to complete. Use `--cleanup` to remove all injected data when done testing.

## Running Production Performance Test

The `test_large_production_performance.sh` script runs a complete production-scale performance test that:
- Creates sample clusters and managed jobs
- Injects production-scale data (12,501 clusters, 12,500 managed jobs)
- Tests performance of `sky status` and `sky jobs queue` commands
- Automatically cleans up all test data

### Usage

```bash
# Run with SQLite (default, local database)
bash tests/load_tests/db_scale_tests/test_large_production_performance.sh

# Run with PostgreSQL database (creates AWS RDS instance)
bash tests/load_tests/db_scale_tests/test_large_production_performance.sh --postgres --restart-api-server

# Run with consolidation mode config only (no PostgreSQL)
bash tests/load_tests/db_scale_tests/test_large_production_performance.sh --restart-api-server
```

**Options:**
- `--postgres` - Create AWS RDS PostgreSQL database and configure API server to use it. Automatically enables `--restart-api-server`.
- `--restart-api-server` - Restart the API server with consolidation mode config (`tests/test_yamls/consolidation_mode_config.yaml`)

**Note:** When `--postgres` is used, the script will:
1. Create an AWS RDS PostgreSQL instance (`skypilot-large-production-test-db`)
2. Export `SKYPILOT_DB_CONNECTION_URI` environment variable
3. Automatically set `--restart-api-server` flag
4. Restart the API server once with both the database connection and consolidation mode config
5. Clean up the RDS instance on script exit

**PostgreSQL Requirements:**
- AWS CLI installed and configured
- AWS credentials with RDS permissions
- Default VPC with at least 2 subnets in different availability zones
- Region can be specified via `AWS_REGION` environment variable (default: `us-east-1`)

## Future Enhancements

- Remote PostgreSQL testing support
- Integration with CI/CD performance benchmarking
- Memory usage profiling
