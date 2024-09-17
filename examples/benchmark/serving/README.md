# Find the best GPUs for your serving workloads with Sky Benchmark

Ever wondered which GPU will give you the highest performance per dollar for your serving workloads?

[Sky Benchmark](https://skypilot.readthedocs.io/en/latest/reference/benchmark/index.html) is a tool for easy measurement of performance and cost of different kinds of cloud resources for your specific workload.

Suppose you want to benchmark a set of hardware choices and corresponding serving configurations `[(HW1, CFG1), ...]`.
You can see how we declare this in serve.yaml’s [`resources.candidates` field](https://github.com/skypilot-org/skypilot/blob/skybench_fixes/examples/benchmark/serving/serve.yaml#L9-L12) and [`run` commands](https://github.com/skypilot-org/skypilot/blob/skybench_fixes/examples/benchmark/serving/serve.yaml#L22-L29) by dynamically switching configuration depending on hardware choice. This effectively simulates serving config `i` for hardware `i`. You can plug in your own config list by modifying `serve.yaml`.  

When you run this benchmark, SkyPilot will:
1. Automatically provision the required GPUs
2. Run the benchmarks and collect results on each GPU
3. Return the **query latency and $/query** for each GPU in a simple format:
   ```bash 
   Legend:
   - #QUERIES: Number of queries sent in the benchmark.
   - SEC/QUERY, $/QUERY: Average time and cost per query.

   RESOURCES  #QUERIES  SEC/QUERY  $/QUERY   
   1x T4:1    50        0.9921     0.000145  
   1x L4:1    50        0.8377     0.000187  
   1x V100:1  50        0.4270     0.000363
   ```


In the following guide, we use Sky Benchmark to find the GPU with the lowest $/query for serving Gemma on vLLM's OpenAI-compatible API server.

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
   
   We have defined a workload generator script `benchmark.py` that generates load to the local vLLM OpenAI API server. We use the `sky_callback.step()` context manager to mark the successful querying of one batch as one step. This allows Sky Benchmark to measure the performance and cost of each step. By default, we use a batch size of 5 queries per batch, but you can configure this in `benchmark.py`. 
   
   `serve.yaml` launches the vLLM API server and invokes the `benchmark.py` script to generate the load. 

3. After the benchmark completes, view the results with `python show_results.py <benchmark name>`. This is a simple script that reads the benchmark results from Sky Benchmark and displays them in a simpler format geared towards serving workloads:
   
   ```bash
   python show_results.py gemma                 
   Legend:
   - #QUERIES: Number of queries sent in the benchmark.
   - SEC/QUERY, $/QUERY: Average time and cost per query.

   RESOURCES  #QUERIES  SEC/QUERY  $/QUERY   
   1x T4:1    50        0.9921     0.000145  
   1x L4:1    50        0.8377     0.000187  
   1x V100:1  50        0.4270     0.000363
   ```

   We see that Nvidia T4 GPU has the lowest $/query for serving Gemma with vLLM, and V100s are 2.3x faster but also cost 2.5x more than T4s.

   Alternatively, you can also use the native `sky bench show` command to view detailed results. In these results, each step will correspond to completion of a batch of 5 queries, configured in `benchmark.py`.
   
   
You can customize the benchmarking script and YAML to suit your specific workload and requirements. For more information, refer to the [Sky Benchmark documentation](https://skypilot.readthedocs.io/en/latest/reference/benchmark/index.html).