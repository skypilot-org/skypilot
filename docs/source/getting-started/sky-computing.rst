.. _sky-computing:

Concept: Sky Computing
===============================

SkyPilot is an open-source Sky Computing framework.

.. In this page, we briefly review the concept of Sky Computing.

.. figure:: ../images/sky-above-clouds-gen.png
   :width: 60%
   :align: center

Problem: Cloud Infra's Explosive Complexity
-------------------------------------------

Today's cloud infra has exploded in complexity.
Organizations are forced to deal with a combinatorially large number of infra choices, across three dimensions:

- **Locations**: 10s of regions and 100s of zones within a single cloud. Teams are also increasingly multicloud (3+ hyperscalers, 10+
  specialized clouds) and multi-cluster.
- **Hardware**: 500+ instance types per cloud; many new accelerators (e.g., GPUs, TPUs).
- **Pricing models**: On-demand, reserved, and preemptible spot instances, each with different pricing and availability.

The search space of ``(locations, hardware, pricing models)`` is combinatorially
large and dynamic, **even within one cloud**.  Seemingly simple tasks like "run jobs in any of my US
regions/clusters with the lowest cost" or "run on either AWS or GCP" become highly costly:

- Valuable engineering hours are invested to mask the differences across infra choices.
- Workloads are forced to run on suboptimal choices (to heuristically simplify the search space), wasting utilization, cost savings, and capacity.

.. TODO: say something about 'abstracting away the infra choices while exploiting cost and capacity differences is hard.'

.. The search space of ``(locations, pricing models, hardware)`` is huge and highly
.. complex. For example, not all locations offer the same hardware; even when a region does,
.. the pricing and availability may be dynamic, depending on the pricing model.

.. As a result, tasks like "run my jobs in any of the US regions in the cheapest
.. way" or "run my jobs on either my AWS or GCP account" become highly complex.
.. Dealing with such **diverse compute** costs significant engineering hours.
.. As heuristics workloads are often forced to run on suboptimal infra choices --- wasting utilization, cost savings, and capacity.

.. Today's cloud ecosystem has significant cloud lock-in. Many workloads are forced to
.. run on specific clouds. As a result, cloud users lose on cost savings, higher capacity, and
.. portability.

Sky Computing
-------------------------

To combat this, *Sky Computing* was recently proposed in two papers from UC Berkeley:
`From Cloud Computing to Sky Computing <https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s02-stoica.pdf>`_ and
`The Sky Above The Clouds <https://arxiv.org/abs/2205.07147>`_ (whitepaper).

In a nutshell, Sky Computing **combines a team's diverse cloud infra into a unified pool**, a "sky".
Sky comes with two components to simplify---and exploit---the complex search space:

- A unified interface to run workloads on different cloud infra.
- An optimizer to find the best  infra choice (cheapest & most available) for each workload.

.. In a nutshell, Sky Computing is a **portable multicloud** paradigm: a Sky layer
.. receives workloads from users and executes them in the "best" (e.g., cheapest
.. and most available) cloud location and infra choice. It combines clouds into a "sky", a unified compute pool.

Both components make using complex cloud infra simple:

- The unified Sky interface allows workloads to be specified once with the same interface, and be able to run on different infra.
- The Sky optimizer cuts across the search space to exploit the (dynamically changing) pricing and availability differences in the compute pool.

.. - The Sky optimizer further saves costs and increases capacity by exploiting the (dynamically changing) pricing and availability differences in the search space.

.. The unified Sky interface frees users from manually ensuring their workloads can run on diverse infra choices, saving valuable engineering time.
.. Sky's optimizer further exploits the complex search space to automatically find infra choices with cheaper cost and higher capacity, cutting across a combinatorially large search space.

.. As such, Sky Computing **enables cloud portability for (certain) workloads**, freeing users from
.. manually porting across clouds and abstracting away their cumbersome differences.

.. One definition of "best placement" is "cheapest and available", especially for
.. AI workloads that need expensive GPU/TPU/accelerator compute.

With Sky, cloud users and their workloads gain the following benefits:

* **Cloud is easier to use**: With the unified interface, infra is simplified & multicloud ready.
* **Lower costs**: Sky optimizes the cost of each workload.  Engineering time is saved from dealing with cloud infra. Large organizations gain pricing leverage due to portability.
* **Higher capacity**: Workloads can now run on a bigger compute pool---all infra choices across locations, hardware, and pricing models.

.. * **Portability**: Cloud infra setup is simplified and automatically multicloud ready.

.. Sky can leverage, but differs from, today's multicloud systems. The latter are typically
.. "partitioned multicloud": for example, in a multicloud organization, workload
.. X always runs in cloud A and workload Y always runs in cloud B --- no portability is involved.

.. Importantly, Sky Computing **also benefits single-cloud users**: Sky
.. simplifies running workloads across a single cloud provider's regions/zones, pricing models, and hardware.

.. can optimize across a single cloud provider's regions/zones, pricing models, and hardware.

SkyPilot and Sky Computing
---------------------------------------------------

SkyPilot was born out of the same `UC Berkeley lab <https://sky.cs.berkeley.edu/>`_  that
proposed the Sky Computing paradigm.
SkyPilot is Sky's first instantiation, and it was started to implement Sky Computing for one important class of workloads: AI and compute-intensive workloads.

Over the last few years, SkyPilot has been widely adopted by ~100s of leading companies and AI teams.
While the initial development team
consisted of Berkeley PhDs/researchers, the SkyPilot community today has grown to
100+ contributors from various organizations.

SkyPilot operates in a BYOC (Bring Your Own Cloud) model, where all resources
are launched in a user's existing cloud accounts, VPCs, and clusters.

SkyPilot is open sourced under the permissive Apache 2 license and under
active development on `GitHub <https://github.com/skypilot-org/skypilot>`_.

.. Why do AI and compute-intensive workloads benefit from Sky Computing?
Why does AI benefit from Sky Computing?
---------------------------------------------------

.. TODO: Convincing arguments here. Tone of 'talking to a new hire'.

.. AI is highly **capacity and cost intensive**, many orders of magnitude higher than prior cloud workloads:

.. - Capacity: AI workloads need GPUs/TPUs/accelerators.  Many teams find AI
..   hardware across locations (e.g., several clusters, regions, or clouds).
.. - Cost: AI accelerators are highly expensive. Many teams use different pricing
..   models (mix of reserved, on-demand, spot instances) and/or different hardware
..   types to save costs.

AI is highly **capacity and cost intensive**, many orders of magnitude more so
than prior cloud workloads. To increase capacity and reduce costs, AI teams are using compute anywhere and in whatever forms they can.

- Locations: AI teams use a mix of hyperscalers (AWS/GCP/Azure/..), GPU
  clouds (CoreWeave/Lambda/..), many regions within a cloud, or several
  Kubernetes clusters.
- Hardware: Different GPU generations for different tasks (e.g., H100 for
  training, A100/L4 for inference); accelerators on hyperscalers (e.g., TPUs, Trainium, Inferentia).
- Pricing models: Teams use a mix of reserved, on-demand, spot GPUs to save costs.

For example, it is common for AI teams
to use reserved H100 on cloud X for training and on-demand/spot L4 on cloud Y
for large-scale batch inference.

As such, AI heavily requires diverse compute, and Sky Computing presents a natural solution.
Sky allows AI teams to **use a single interface to easily and portably run AI** workloads on their diverse compute.
Also, Sky cuts down the large AI bills and enlarges capacity by auto-optimizing workloads to the best compute choices.

.. These benefits apply even if a team uses one cloud provider.

..  without tedious infra burden on the AI and infra teams.

.. Sky Computing naturally **unifies diverse compute** into a simple interface, solving this challenge.
.. By running workloads with Sky's unified interface, users can *easily and portably* run AI workloads on diverse compute,
.. thereby increasing capacity and lowering costs. Importantly, these benefits come
.. without tedious infra burden on the AI and infra teams.

.. Thus, utilizing diverse compute across locations, pricing
.. models, and hardware is critical for AI workloads to increase capacity and reduce costs.

.. However, the search space of (locations, pricing models, hardware) is large and
.. complex to optimize, so using diverse compute across these dimensions is a difficult
.. infra challenge.

.. However, using diverse compute across these dimensions is a hard infra
.. challenge: the search space of (locations, pricing models, hardware) is large and
.. complex to optimize.

.. Thus, utilizing diverse compute across locations, pricing
.. models, and hardware is critical for AI workloads to increase capacity and reduce costs. However,
.. this is a hard infra challenge: the search space of (cloud(s), pricing models, hardware) is huge and difficult to optimize.



.. It allows AI workloads to *easily and portably utilize diverse compute infra*,

.. What about data?
.. ---------------------------------------------------

.. TODO: Talk about data locality.

What if I have a single cloud?
---------------------------------------------------

Just like autonomous driving has different levels of autonomy (e.g., Level 1-5), one can adopt Sky Computing and SkyPilot in increasing "levels" and benefits.

**For users on a fixed cluster** (e.g., Kubernetes, Slurm), SkyPilot provides:

- A simple interface to submit and manage AI workloads, tailored to AI users' ergonomics.
- Support for dev clusters, jobs, and serving on your cluster.
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

- Combines all of your infra into a unified pool, for higher utilization, cost savings, and capacity.
- Plus all of the benefits above.



Learning more
---------------------------------------------------

Today, the systems and cloud community at UC Berkeley --- and beyond --- have
produced several follow-up projects to enrich the Sky Computing stack:

- `Can't Be Late <https://www.usenix.org/conference/nsdi24/presentation/wu-zhanghao>`_: Advanced spot instance scheduling policy for SkyPilot (NSDI '24 Best Paper).
- `SkyPlane <https://github.com/skyplane-project/skyplane>`_: Open-source tool for fast and cost-effective inter-cloud data transfer.
- `CloudCast <https://www.usenix.org/conference/nsdi24/presentation/wooders>`_: High-throughout, cost-aware cross-region and cross-cloud multicast.
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
