.. _benchmark-overview:

Benchmarking Hardware
================================================

SkyPilot allows **easy measurement of performance and cost of different kinds of cloud resources** through the benchmark feature.
With minimal effort, you can find the right cloud resource for your task that fits your performance goals and budget constraints.

For example, say you want to fine-tune a BERT model and you do not know which GPU type is the best for you.
With SkyPilot Benchmark, you can quickly run your task on different types of VMs and get a benchmark report like the following:

.. code-block:: bash

    Legend:
    - STEPS: Number of steps taken.
    - SEC/STEP, $/STEP: Average time (cost) per step.
    - EST(hr), EST($): Estimated total time (cost) to complete the benchmark.

    CLUSTER            RESOURCES                                STATUS      DURATION  SPENT($)  STEPS   SEC/STEP  $/STEP    EST(hr)  EST($)
    sky-bench-bert-0  1x Azure(Standard_NC6_Promo, {'K80': 1})  TERMINATED  12m 48s   0.0384    1415    1.1548    0.000058  10.60    1.91
    sky-bench-bert-1  1x AWS(g4dn.xlarge, {'T4': 1})            TERMINATED  14m 2s    0.1230    2387    0.6429    0.000094  5.92     3.11
    sky-bench-bert-2  1x AWS(g5.xlarge, {'A10G': 1})            TERMINATED  13m 57s   0.2339    7423    0.1859    0.000052  1.75     1.76
    sky-bench-bert-3  1x GCP(n1-highmem-8, {'V100': 1})         TERMINATED  13m 45s   0.6768    7306    0.2005    0.000165  1.87     5.51

The report shows the benchmarking results of 4 VMs each with a different GPU type.
Based on the report, you can pick the VM with either the lowest cost (``EST($)``) or the fastest execution time (``EST(hr)``), or find a sweet spot between them.
In this example, AWS g5.xlarge (NVIDIA A10G GPU) turns out to be the best choice in terms of both cost and time.

Using SkyPilot Benchmark
------------------------

A part of the SkyPilot Benchmark report relies on the :ref:`SkyCallback <benchmark-skycallback>` library instrumented in the training code to report step completion.
Depending on the level of detail required by you in the benchmark report, SkyPilot Benchmark can be used in two modes:

1. Without SkyCallback - You can get a basic benchmark report using SkyPilot Benchmark :ref:`benchmark-cli`. **This requires zero changes in your code**.
2. With SkyCallback - You can get a more detailed benchmark report **by a few lines of code changes**. Please refer to :ref:`SkyCallback <benchmark-skycallback>`.

Table of Contents
-----------------
.. toctree::
   cli
   config
   callback
