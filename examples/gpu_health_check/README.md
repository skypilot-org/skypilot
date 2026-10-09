# GPU Health Check

Check the GPU you were given before you trust it with a long job.

GPUs from marketplaces and spot pools sometimes arrive with faulty memory. [Pantheon](https://pantheongpu.com) is an open-source (Apache-2.0) GPU test suite. This example runs its `diagnostics` suite on the GPU that SkyPilot provisions and fails the task if a test finds corrupted memory.

Files in this example:

- `gpu_health_check.yaml`: the SkyPilot task. It installs Pantheon from PyPI and runs the suite on every GPU of the node.

## Run it

```bash
cd examples/gpu_health_check
sky launch -c gpucheck gpu_health_check.yaml
sky launch -c gpucheck gpu_health_check.yaml --gpus L4:1 --infra vast   # a specific GPU or cloud
sky down gpucheck
```

The suite has five tests, 30 seconds each by default (`--env DURATION=60` to change it):

| Test | What it does |
| :--- | :--- |
| `march_test` | A March C- pass over the free memory: an ordered read-then-write of every cell, which exposes coupling between neighbouring cells |
| `galpat` | Flips one cell of a region and reads it against every other cell, which exposes address-decoder faults and far-apart coupling |
| `memory_hammer` | Reads pairs of aggressor addresses around an untouched victim, then checks the whole buffer for disturbed cells |
| `memory_retention` | Writes a payload, leaves it untouched for an interval, then verifies it, which exposes cells that lose charge over time |
| `ras_validator` | Repeated non-temporal reads of a pristine payload, which expose uncorrectable errors, silent corruption and correction latency |

The first run builds the test kernels for the GPU it finds, which took about three minutes on the machine below; the five tests then take about three more.

## Example output

`sky launch -c gpucheck gpu_health_check.yaml --infra vast --gpus RTX4090:1` on Vast (SkyPilot 0.14.0). Vast gave us an RTX PRO 6000 Blackwell Max-Q. The job took 6 minutes:

```
GPU 0: [NVIDIA] NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition | 97887 MB VRAM | GDDR7 (Samsung)
[PANTHEON] --- STARTING TEST: MARCH_TEST ---
Verification: PASS (0 march errors over 16 passes)
[RESULT] GPU 0 | 33275800000.0 march-ops/s | 52.4C Avg / 60.0C Max | 233.2W Avg / 257.6W Max
[PANTHEON] --- STARTING TEST: GALPAT ---
Verification: PASS (0 gallop errors over 265 passes)
[RESULT] GPU 0 | 9432860000.0 gallop-reads/s | 59.1C Avg / 60.0C Max | 102.7W Avg / 107.0W Max
[PANTHEON] --- STARTING TEST: MEMORY_HAMMER ---
Verification: PASS (0 disturbed cells)
[RESULT] GPU 0 | 345064000000.0 aggressor-reads/s | 70.2C Avg / 76.0C Max | 263.2W Avg / 277.5W Max
[PANTHEON] --- STARTING TEST: MEMORY_RETENTION ---
Verification: PASS (0 retention errors after 30s)
[RESULT] GPU 0 | 95723.0 retained-MiB | 66.2C Avg / 71.0C Max | 61.6W Avg / 105.1W Max
[PANTHEON] --- STARTING TEST: RAS_VALIDATOR ---
Verification: PASS (0 errors)
[RESULT] GPU 0 | 1643.37 GB/s | 70.5C Avg / 76.0C Max | 263.5W Avg / 278.9W Max
Job finished (status: SUCCEEDED).
```

## Gate a pipeline on it

The task exits non-zero when a test detects corrupted memory or fails to run, so SkyPilot marks the job `FAILED`. To see the gate fire, inject one fault into one test on the running cluster:

```bash
sky exec gpucheck gpu_health_check.yaml --env SUITE=march_test --env EXTRA_ARGS=--inject_error
```

The same cluster answers with:

```
[PANTHEON] Warning: SDC Fault Injection is ACTIVE!
Verification: FAIL (16 march errors over 16 passes)
[RESULT] GPU 0 | 0.0 ERR | 69.2C Avg / 75.0C Max | 252.2W Avg / 279.3W Max
ERROR: Job 2 failed with return code list: [1]
Job finished (status: FAILED).
```

To check a GPU before a real task, launch this task first and then submit your own task to the same cluster with `sky exec gpucheck my_task.yaml`. It runs in the same CUDA image, so it does not need `apt` or `sudo` of its own. `sky exec` runs only the `run` section of a task and skips its `setup` and file mounts, so a task that needs either should be started with `sky launch -c gpucheck my_task.yaml` instead.

## Notes

- The exit code covers failed verification and failed runs. Thermal throttling, PCIe link errors and correctable ECC counts appear in the printed table and the report, but they do not change the exit code.
- Reports are written to `~/pantheon-run/database/` on the cluster: `rsync -Pavz gpucheck:pantheon-run/database/ ./reports/`.
- `--gpu all` tests every GPU of the node at the same time, so a multi-GPU node needs a power supply that can feed them all at once.
