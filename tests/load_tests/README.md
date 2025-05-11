# Load Testing Tools

This folder contains tools for load testing and profiling the SkyPilot API server.

* System Profiling (`sys_profiling.py`): monitors system resource usage (CPU and memory) over time
* Load Testing (`test_load_on_server.py`): sends concurrent requests to stress test the SkyPilot API server

> **Note**: The load testing workload is simple and may not reflect the usage of the SkyPilot API server in real-world scenarios.
> You may consider running part of or all smoke tests to get a more accurate measurement.

## Instructions

### Load Testing

1. Start the SkyPilot API server on a remote machine
```bash
sky api stop && sky api start
```
2. Run the profiling script on the server machine
```bash
python tests/load_tests/sys_profiling.py
```
3. Run the load testing script locally to send requests to the server
```bash
python tests/load_tests/test_load_on_server.py -n 50 -r all --cloud aws
```
4. Once the load testing is done, terminate the profiling process to get a resource usage summary printed in the terminal, e.g.
```bash
==================================================
MONITORING SUMMARY
==================================================
Duration: 1481.7 seconds (0.41 hours)

BASELINE USAGE:
Baseline CPU: 1.2%
Baseline Memory: 2.99GB (21.6%)

PEAK USAGE:
Peak CPU: 96.9%
Peak Memory: 11.78GB (79.0%)
Memory Delta: 8.8GB
Peak Non-blocking Executor Memory: 0.37GB
Peak Non-blocking Executor Memory Average: 0.23GB
Peak Blocking Executor Memory: 0.35GB
Peak Blocking Executor Memory Average: 0.30GB

AVERAGE USAGE:
Average CPU: 11.5%
Average Memory: 63.1%
==================================================
```
5. Update the `_LONG_WORKER_MEM_GB` and `_SHORT_WORKER_MEM_GB` constants in `sky/server/requests/executor.py` based on the peak memory usage of the blocking and non-blocking workers.

### Benchmarking

The load testing script can be used to benchmark.
The caveat here is spinning up high concurrent `sky` CLI processes can greatly increase the latency on client side.
It is recommended to run benchmark using `--api` args if you want the benchmark focus on the API performance.

```bash
python tests/load_tests/test_load_on_server.py -n 100 --api status
```

### Use distributed clients to benchmark

When benchmarking with the high concurrency, the client can become a bottleneck.
To address this, we can distribute the load to multiple clients to focus the benchmark on the API server.

```bash
# -t: number of client instances
# --cpus: number of CPU cores to use for each client
# --memory: GB of memory to use for each client
# --url: URL of the API server to benchmark, it is recommended to launch the clients on a different API server to avoid interference
# <ARGS_FOR_TEST_LOAD_ON_SERVER>: args for test_load_on_server.py, refer to the previous sections for more details
python tests/load_tests/test_distribute_load_on_server.py -t 10 --cpus 2+ --memory 8+ --url <API_SERVER_URL> <ARGS_FOR_TEST_LOAD_ON_SERVER>
```
