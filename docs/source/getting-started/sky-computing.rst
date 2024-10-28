.. _sky-computing:

Concept: Sky Computing
===============================

SkyPilot is an open-source Sky Computing framework.

.. In this page, we briefly review the concept of Sky Computing.

.. figure:: ../images/sky-above-clouds-gen.png
   :width: 60%
   :align: center

Overview of Sky Computing
----------------------

Today's cloud ecosystem has significant cloud lock-in. Many workloads are forced to
run on specific clouds. As a result, cloud users lose on cost savings, higher capacity, and
portability.

To combat this, *Sky Computing* was recently proposed in two papers from UC Berkeley:
`From Cloud Computing to Sky Computing <https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s02-stoica.pdf>`_ and
`The Sky Above The Clouds <https://arxiv.org/abs/2205.07147>`_ (whitepaper).

In a nutshell, Sky Computing is a **portable multicloud** paradigm: a Sky layer
receives workloads from users and executes them in the "best" (e.g., cheapest
and most available) cloud location and infra choice. It combines clouds into a "sky", a unified compute pool.

As such, Sky Computing **enables cloud portability for (certain) workloads**, freeing users from
manually porting across clouds and abstracting away their cumbersome differences.

.. One definition of "best placement" is "cheapest and available", especially for
.. AI workloads that need expensive GPU/TPU/accelerator compute.

Cloud users and their workloads gain the following benefits:

* **Portability** across regions or clouds: Cloud infra setup is simplified and multicloud ready.
* **Lower costs**: Sky optimizes the cost of each workload. Users gain pricing leverage due to portability.
* **Higher capacity**: Workloads can utilize diverse compute across locations, pricing models, and hardware.

.. Sky can leverage, but differs from, today's multicloud systems. The latter are typically
.. "partitioned multicloud": for example, in a multicloud organization, workload
.. X always runs in cloud A and workload Y always runs in cloud B --- no portability is involved.

Importantly, Sky Computing **also benefits single-cloud users**: Sky
simplifies running workloads across a single cloud provider's regions/zones, pricing models, and hardware.

.. can optimize across a single cloud provider's regions/zones, pricing models, and hardware.

SkyPilot and Sky Computing
---------------------------------------------------

SkyPilot was born out of the same `UC Berkeley lab <https://sky.cs.berkeley.edu/>`_  that
proposed the Sky Computing paradigm.
SkyPilot is Sky's first instantiation, and it was started to showcase Sky Computing's benefits for AI and compute-intensive
workloads.

Over the last few years, SkyPilot has grown to become widely adopted by AI teams in the industry. Today, it
is being used by ~10s to 100s of leading organizations. While the initial development team
consisted of Berkeley PhDs and researchers, the SkyPilot community today has
grown to ~100 open-source contributors from various organizations.

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
than prior cloud workloads. To increase capacity and reduce costs, AI teams
use diverse compute across:

- Locations: Teams have access to a mix of hyperscalers (AWS/GCP/Azure/OCI), AI GPU clouds, different regions within a cloud, and/or several Kubernetes or Slurm clusters.
- Pricing models: Teams use a mix of reserved, on-demand, spot instances to save costs.
- Hardware: Teams use different accelerators (e.g., H100/A100, TPUs, Trainium/Inferentia).

.. Thus, utilizing diverse compute across locations, pricing
.. models, and hardware is critical for AI workloads to increase capacity and reduce costs.

However, the search space of (locations, pricing models, hardware) is large and
complex to optimize, so using diverse compute across these dimensions is a difficult
infra challenge.

.. However, using diverse compute across these dimensions is a hard infra
.. challenge: the search space of (locations, pricing models, hardware) is large and
.. complex to optimize.

.. Thus, utilizing diverse compute across locations, pricing
.. models, and hardware is critical for AI workloads to increase capacity and reduce costs. However,
.. this is a hard infra challenge: the search space of (cloud(s), pricing models, hardware) is huge and difficult to optimize.

Sky Computing naturally **unifies diverse compute** into a simple interface, solving this challenge.
By running workloads with Sky's unified interface, users can *easily and portably* run AI workloads on diverse compute,
thereby increasing capacity and lowering costs. Importantly, these benefits come
without tedious infra burden on the AI and infra teams.

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

To learn more about SkyPilot, you can refer to the `SkyPilot NSDI 2023 paper
<https://www.usenix.org/system/files/nsdi23-yang-zongheng.pdf>`_ and `talk
<https://www.usenix.org/conference/nsdi23/presentation/yang-zongheng>`_.

To learn more about Sky Computing, see the `Sky Computing whitepaper <https://arxiv.org/abs/2205.07147>`_.


Getting involved
---------------------------------------------------

**Try out SkyPilot**: Experience Sky Computing in your cloud(s) in 5 minutes via :ref:`Quickstart <quickstart>`.

**Share your feedback**: Chat with the team on `SkyPilot Slack <http://slack.skypilot.co>`_ or drop a note on our `GitHub <https://github.com/skypilot-org/skypilot>`_.

**Contributing**: We welcome contributions from the community! See `CONTRIBUTING <https://github.com/skypilot-org/skypilot/blob/master/CONTRIBUTING.md>`_.
