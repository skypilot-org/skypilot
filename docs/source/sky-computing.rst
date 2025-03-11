.. _sky-computing:

Concept: Sky Computing
===============================

SkyPilot is an open-source Sky Computing framework.

.. figure:: images/sky-above-clouds-gen.jpg
   :width: 60%
   :align: center

Problem: Cloud infra's explosive complexity
-------------------------------------------

Today's cloud infra has exploded in complexity.
Organizations are forced to deal with a combinatorially large number of infra choices, across three dimensions:

- **Locations**: 10s of regions and 100s of zones within a single cloud. Teams are also increasingly multi-cluster and multicloud (3+ hyperscalers, 10+
  specialized clouds).
- **Hardware**: 500+ instance types per cloud; many new accelerators (e.g., GPUs, TPUs).
- **Pricing models**: On-demand, reserved, and preemptible spot instances, each with different pricing and availability.

The search space of ``(locations, hardware, pricing models)`` is combinatorially
large, **even within one cloud**.
It is also dynamic, since availability and pricing change over time and differ by location.
Seemingly simple tasks like "run jobs in any of my US
regions/clusters with the lowest cost" or "monitor and manage jobs on both AWS and GCP" become highly costly:

- Valuable engineering hours are invested to mask the differences across infra choices.
- Workloads are forced to run on suboptimal choices (to heuristically simplify the search space), wasting utilization, cost savings, and capacity.

Sky Computing
-------------------------

To combat this, *Sky Computing* was recently proposed in two papers from UC Berkeley:
`From Cloud Computing to Sky Computing <https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s02-stoica.pdf>`_ and
`The Sky Above The Clouds <https://arxiv.org/abs/2205.07147>`_ (whitepaper).

In a nutshell, Sky Computing **combines a team's diverse cloud infra into a unified pool**, a "sky".
Sky comes with two components to simplify---and exploit---the complex search space:

- A unified interface to run workloads on different cloud infra.
- An optimizer to find the best  infra choice (cheapest & most available) for each workload.

Both components make using complex cloud infra simple:

- The unified Sky interface allows workloads to be specified once with the same interface, and be able to run on different infra.
- The Sky optimizer cuts across the search space to exploit the (dynamically changing) pricing and availability differences in the compute pool.

With Sky, cloud users and their workloads gain the following benefits:

* **Cloud is easier to use**: With the unified interface, infra is simplified & multicloud ready.
* **Lower costs**: Engineering time is saved from dealing with cloud infra. Sky optimizes the cost of each workload. Large organizations gain pricing leverage due to portability.
* **Higher capacity**: Workloads can now run on a bigger compute pool, with many choices of locations, hardware, and pricing models.

Why does AI benefit from Sky Computing?
---------------------------------------------------

AI is highly **capacity and cost intensive**, many orders of magnitude more so
than prior cloud workloads. To increase capacity and reduce costs, AI teams are using compute anywhere and in whatever forms they can.

- Locations: AI teams use a mix of hyperscalers (AWS/GCP/Azure/..), GPU
  clouds (CoreWeave/Lambda/..), many regions within a cloud, and/or many
  Kubernetes clusters.
- Hardware: Different GPU generations for different tasks (e.g., H100 for
  training, L4 for inference); AMD GPUs; accelerators on hyperscalers (e.g., TPUs, Trainium, Inferentia).
- Pricing models: Teams use a mix of reserved, on-demand, spot GPUs to save costs.

These choices often interleave: e.g., it is common for AI teams to use reserved H100 on cloud X for training and spot L4 on cloud Y
for large-scale batch inference.
Therefore, AI workloads inherently require managing many compute choices in the complex search space.

Sky Computing presents a natural solution.
Sky offers AI teams **a unified interface to easily and portably run AI** on their diverse compute.
Further, Sky intelligently orchestrates across a team's AI compute choices, providing large cost savings and higher compute capacity.

SkyPilot and Sky Computing
---------------------------------------------------

SkyPilot was born out of the same `UC Berkeley lab <https://sky.cs.berkeley.edu/>`_  that
proposed Sky Computing.
SkyPilot is Sky's first instantiation, and it was started to implement Sky Computing for one important class of workloads: AI and compute-intensive workloads.

Over the last few years, SkyPilot has been widely adopted by ~100s of leading companies and AI teams.
While the initial development team
consisted of Berkeley PhDs/researchers, the SkyPilot community today has grown to
100+ `contributors <https://github.com/skypilot-org/skypilot/graphs/contributors>`_ from many organizations.

SkyPilot operates in a BYOC (Bring Your Own Cloud) model, where all resources
are launched in a user's existing cloud accounts, VPCs, and clusters.

SkyPilot is open sourced under the permissive Apache 2 license and under
active development on `GitHub <https://github.com/skypilot-org/skypilot>`_.

What if I have a single cloud: Levels of Sky Computing
------------------------------------------------------

Just like autonomous driving has different levels of autonomy (e.g., Level 1-5), one can adopt Sky Computing and SkyPilot in increasing "levels" and benefits.

**For users on a fixed cluster** (e.g., Kubernetes, Slurm), SkyPilot provides:

- A simple interface to submit and manage AI workloads, tailored to AI users' ergonomics.
- Support for clusters, jobs, and serving on your cluster.
- Cost savings: Autostop, queueing, and higher hardware utilization.
- Future-proofness: No retooling when you add other clusters or clouds in the future.

**For users on one cloud's single region/zone**, SkyPilot provides:

- Auto-retry, auto-fallback provisioner: Specify many hardware fallback targets and SkyPilot will auto-optimize and auto-retry to combat GPU shortage.
- Battle-tested job recovery, including training and serving on spot instances.
- :ref:`Simple workload packaging <quickstart>`: Wrap your existing AI projects in a simple SkyPilot YAML and have all infra tasks handled for you.
- Plus all of the benefits above.

**For users on one cloud's multiple regions**, SkyPilot provides:

- Support for a single job to utilize multiple regions for GPU availability & faster recovery.
- Support for a model's replicas to span multiple regions for availability & cost savings.
- Plus all of the benefits above.

**For users on multiple clouds or clusters**, SkyPilot

- Combines all of your infra into a unified pool (your *Sky*), for higher utilization, cost savings, and capacity.
- Plus all of the benefits above.



Learning more
---------------------------------------------------

Today, the systems community at UC Berkeley --- and beyond --- have
produced several follow-up projects to expand the Sky Computing stack:

- `SkyServe <https://arxiv.org/abs/2411.01438>`_: SkyPilot's cross-region, cross-cloud AI serving library (:ref:`user docs <sky-serve>`).
- `Can't Be Late <https://www.usenix.org/conference/nsdi24/presentation/wu-zhanghao>`_: Advanced spot instance scheduling policy for SkyPilot (NSDI '24 Best Paper).
- `Skyplane <https://github.com/skyplane-project/skyplane>`_: Open-source tool for fast and cost-effective cross-cloud data transfer.
- `Cloudcast <https://www.usenix.org/conference/nsdi24/presentation/wooders>`_: High-throughout, cost-aware cross-region and cross-cloud multicast.
- `FogROS2 <https://berkeleyautomation.github.io/FogROS2/about>`_: Open-source cloud robotics platform leveraging Sky Computing via SkyPilot.
- …and a few more in the pipeline.

To learn more about SkyPilot, refer to the `project announcement blog post <https://blog.skypilot.co/introducing-skypilot/>`_, or the   `SkyPilot NSDI 2023 paper
<https://www.usenix.org/system/files/nsdi23-yang-zongheng.pdf>`_ and `talk
<https://www.usenix.org/conference/nsdi23/presentation/yang-zongheng>`_.

To learn more about Sky Computing, see the `Sky Computing whitepaper <https://arxiv.org/abs/2205.07147>`_.


Getting involved
---------------------------------------------------

**Try out SkyPilot**: Experience Sky Computing in your cloud(s) in 5 minutes via :ref:`Quickstart <quickstart>`.

**Share your feedback**: Chat with the team on `SkyPilot Slack <http://slack.skypilot.co>`_ or drop a note on our `GitHub <https://github.com/skypilot-org/skypilot>`_.

**Contributing**: We welcome contributions from the community! See `CONTRIBUTING <https://github.com/skypilot-org/skypilot/blob/master/CONTRIBUTING.md>`_.
