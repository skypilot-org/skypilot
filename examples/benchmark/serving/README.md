# Find the best GPUs for your serving workloads with Sky Benchmark

Ever wondered which GPU will give you the highest performance per dollar for your serving workloads?

[Sky Benchmark](https://skypilot.readthedocs.io/en/latest/reference/benchmark/index.html) is a tool for easy measurement of performance and cost of different kinds of cloud resources for your specific workload.

* **1-click launch** large-scale benchmarks across multiple cloud providers and GPU types.
* **Get $/query and performance metrics** for different GPUs on your specific workload.
* **Find the right cloud resources** for your task that fit your performance goals and budget constraints.

In this guide, we use Sky Benchmark to find the GPU with the lowest $/query for serving Gemma on vLLM's OpenAI-compatible API server.

## Prerequisites

1. AWS access is required. Benchmark results are written and fetched from S3.
2. Install the latest SkyPilot and check your setup of the cloud credentials:
    ```bash
    pip install "skypilot-nightly[aws,gcp,azure]"
    sky check
    ```

## Benchmarking vLLM OpenAI API serving across GPU types

1. Configure the resources for the benchmark in `serve.yaml`. For example, to benchmark Nvidia T4, L4 and V100 GPUs, use:
    ```yaml
    resources:
      candidates: # Configure GPUs/clouds you want to benchmark here
        - { accelerators: T4:1 }
        - { accelerators: L4:1 }
        - { accelerators: V100:1 }
    ```

2. Launch the benchmark with `sky bench launch`:
   ```bash
   $ sky bench launch -b gemma serve.yaml
   Benchmarking a task from YAML spec: serve.yaml
   Benchmarking a task on candidate resources (benchmark name: gemma):
   --------------------------------------------------------------------------------------------------------
    CLUSTER              CLOUD   # NODES   INSTANCE        vCPUs   Mem(GB)   ACCELERATORS   PRICE ($/hr)
   --------------------------------------------------------------------------------------------------------
    sky-bench-gemma-0    AWS     1         g4dn.xlarge     4       16        T4:1           0.53
    sky-bench-gemma-1    AWS     1         g6.xlarge       4       16        L4:1           0.80
    sky-bench-gemma-2    AWS     1         p3.2xlarge      8       61        V100:1         3.06
   --------------------------------------------------------------------------------------------------------
   ```
   
   We have defined a workload generator script `benchmark.py` that generates load to the local vLLM OpenAI API server. We use the `sky_callback.step()` context manager to mark the successful querying of one batch as one step. This allows Sky Benchmark to measure the performance and cost of each step. 
   
   `serve.yaml` launches the vLLM API server aand invokes the `benchmark.py` script to generate the load. 

3. After the benchmark completes, view the results with `sky bench show`:

   <!---TODO: we need to look into renaming some of the table titles to fit serving-->
   ```bash
   $ sky bench show gemma
   Processing 3 benchmark results ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:00
   Legend:
   - #STEPS: Number of steps taken.
   - SEC/STEP, $/STEP: Average time (cost) per step.
   - EST(hr), EST($): Estimated total time (cost) to complete the benchmark.
   
   CLUSTER             RESOURCES                        STATUS    DURATION  SPENT($)  #STEPS  SEC/STEP  $/STEP    EST(hr)  EST($)
   sky-bench-gemma-0   1x AWS(g4dn.xlarge, {'T4': 1})   FINISHED  52s       0.0077    10      4.9604    0.000725  0.01     0.01
   sky-bench-gemma-1   1x AWS(g6.xlarge, {'L4': 1})     FINISHED  46s       0.0104    10      4.1886    0.000936  0.01     0.01
   sky-bench-gemma-2   1x AWS(p3.2xlarge, {'V100': 1})  FINISHED  23s       0.0199    10      2.1349    0.001815  0.01     0.02
   ```
   
   In these results, each step corresponds to completion of a batch of 5 queries. This can be configured in `benchmark.py`.

   We see that Nvidia T4 GPU has the lowest $/query for serving Gemma with vLLM, and V100s are > 2x faster but also cost nearly 3x more than T4s. 

You can customize the benchmarking script and YAML to suit your specific workload and requirements. For more information, refer to the [Sky Benchmark documentation](https://skypilot.readthedocs.io/en/latest/reference/benchmark/index.html).