# Scale Testing Tools

This folder contains tools for testing SkyPilot operations at large scale. These tests are designed to evaluate performance and behavior with significant amounts of data:

- **2,000 active clusters** in the clusters table
- **10,000 terminated clusters** in the cluster_history table (2,000 recent + 8,000 older)

The scale tests help understand performance bottlenecks and database query performance under realistic enterprise scale.

## Important Notes

 **Local Testing Warning**: The automated tests in this folder are currently set up to run locally with a SQLite backend. This means they will modify your local SkyPilot databases:

- `~/.sky/state.db` (clusters and cluster_history tables)
- `~/.sky/spot_jobs.db` (spot and job_info tables)

The tests will add many entries to these tables during execution but will clean up automatically at the end.

## Test Data Injection

The scale tests inject realistic test data by:

1. **Schema-based generation**: Generate test data from database schema definitions in `sky/global_user_state.py` without requiring existing data
2. **Data integrity**: Maintain proper foreign key relationships and realistic timestamps (terminated clusters have different activity times for time-based filtering tests)
3. **Batch processing**: Insert data in batches for optimal performance
4. **Automatic cleanup**: Remove all test data after test completion

The tests can populate empty databases by analyzing table schemas and generating appropriate test values for each field type.

## Test Categories

### Database Scale Tests
- **Active Clusters**: `get_clusters()` performance with 2,000 active clusters
- **Cluster History**: `get_clusters_from_history()` performance with time-based filtering (10 days vs 30 days)

### API Performance Tests
- **CLI Commands**: `sky status` performance at scale

## Running Performance Benchmarks

> **Note**: Make sure to backup your local SkyPilot databases before running scale tests if you have important data.

```bash
# Backup your databases (recommended)
cp ~/.sky/state.db ~/.sky/state.db.backup
cp ~/.sky/spot_jobs.db ~/.sky/spot_jobs.db.backup
```

### Option 1: Quick Performance Benchmark (Recommended)

For a quick performance overview with detailed timing information:

```bash
# Run the standalone benchmark script with verbose output
python tests/scale_tests/run_scale_test.py
```

This will:
- Inject 2,000 active clusters + 10,000 terminated clusters
- Benchmark `get_clusters()`, `get_clusters_from_history(days=10)`, and `get_clusters_from_history(days=30)`
- Show performance metrics (duration, rate) for each operation
- Automatically clean up test data

### Option 2: Full Test Suite with Assertions

For comprehensive testing with performance assertions and regression detection:

```bash
# Run pytest with verbose output and performance logging
pytest tests/scale_tests/test_scale.py -n 1 -v -s --tb=short
```

This will:
- Run all scale tests with performance assertions
- Ensure operations complete within acceptable time limits
- Detect performance regressions
- Show detailed test results and performance summary

## Performance Monitoring

Scale tests are instrumented to measure:

- **Database query execution times** for `get_clusters()` and `get_clusters_from_history()`
- **API response times** for `sky status` command at scale
- **Data injection performance** (batch insert rates)

Results are logged to help identify performance regressions and optimization opportunities.

## Future Enhancements

- Remote PostgreSQL testing support
- Configurable dataset sizes
- Integration with CI/CD performance benchmarking
- Memory usage profiling