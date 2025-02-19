# Load Testing Tools

This folder contains tools for load testing and profiling the SkyPilot API server.

* System Profiling (`sys_profiling.py`): monitors system resource usage (CPU and memory) over time
* Load Testing (`test_load_on_server.py`): sends concurrent requests to stress test the SkyPilot API server

## Instructions

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
python tests/load_tests/test_load_on_server.py -n 100 -r launch
```
4. Once the load testing is done, terminate the profiling process to get a resource usage summary printed in the terminal, e.g.
```bash
==================================================
MONITORING SUMMARY
==================================================
Duration: 28.6 seconds (0.01 hours)

BASELINE USAGE:
Baseline CPU: 2.5%
Baseline Memory: 14.17GB (47.6%)

PEAK USAGE:
Peak CPU: 100.0%
Peak Memory: 15.88GB (53.2%)
Memory Delta: 1.7GB

AVERAGE USAGE:
Average CPU: 19.3%
Average Memory: 49.5%
==================================================
```

