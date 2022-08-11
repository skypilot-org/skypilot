.. _benchmark-overview:

Benchmark
================================================

SkyPilot supports **easy benchmarking of cloud resources**.
With minimal effort, you can find the right cloud resource for your task and **save significant cost**.

Here is an example task that fine-tunes BERT on a question answering dataset.
With SkyPilot Benchmark, you can quickly run your task on different types of VMs and get a simple benchmark report like the following:

.. code-block:: bash

    Legend:
    - STEPS: Number of steps taken.
    - SEC/STEP, $/STEP: Average time (cost) per step.
    - EST(hr), EST($): Estimated total time (cost) to complete the benchmark.

    CLUSTER            RESOURCES                                 STATUS      DURATION  SPENT($)  STEPS   SEC/STEP  $/STEP    EST(hr)  EST($)  
    sky-bench-squad-0  1x Azure(Standard_NC6_Promo, {'K80': 1})  TERMINATED  12m 48s   0.0384    1415    1.1548    0.000058  10.60    1.91    
    sky-bench-squad-1  1x AWS(g4dn.xlarge, {'T4': 1})            TERMINATED  14m 2s    0.1230    2387    0.6429    0.000094  5.92     3.11    
    sky-bench-squad-2  1x AWS(g5.xlarge, {'A10G': 1})            TERMINATED  13m 57s   0.2339    7423    0.1859    0.000052  1.75     1.76      
    sky-bench-squad-3  1x GCP(n1-highmem-8, {'V100': 1})         TERMINATED  13m 45s   0.6768    7306    0.2005    0.000165  1.87     5.51

The report shows the benchmarking results of 4 VMs each with a different GPU type.
Based on the report, you can pick the VM with either the lowest cost (``EST($)``) or the fastest execution time (``EST(hr)``), or find a sweet spot between them.
In this example, AWS g5.xlarge (NVIDIA A10G GPU) turns out to be the best choice in terms of both cost and time.

Table of Contents
-------------------
.. toctree::
   cli
   config
   callback
