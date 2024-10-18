.. _sky-computing:

Concept: Sky Computing
===============================

SkyPilot is an open-source Sky Computing framework.

In this page, we briefly review the background of the Sky Computing paradigm.

Overview of Sky Computing
----------------------

Sky Computing was recently proposed in the following two UC Berkeley papers:
`From Cloud Computing to Sky Computing <https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s02-stoica.pdf>`_ (HotOS'21) and
`The Sky Above The Clouds <https://arxiv.org/abs/2205.07147>`_ (arXiv; whitepaper).

In a nutshell, Sky Computing is a **unified multicloud** paradigm: a Sky layer
receives workloads from users and executes them in the "best" (e.g., cheapest
and most available) cloud location and infra choice.

As such, Sky Computing **enables cloud portability for (certain) workloads**, freeing users from
manually porting across clouds and abstracting away their cumbersome differences.

.. One definition of "best placement" is "cheapest and available", especially for
.. AI workloads that need expensive GPU/TPU/accelerator compute.

Cloud users and their workloads gain the following benefits:

* **Lower costs**: Sky optimizes the cost of each workload. Users gain pricing leverage due to portability.
* **Higher capacity**: Users now have easy access to diverse compute across locations, pricing models, and hardware.
* **Portability** across regions or clouds.

Sky can leverage, but differs from, today's multicloud systems. The latter are typically
"partitioned multicloud": for example, in a multicloud organization, workload
X always runs in cloud A and workload Y always runs in cloud B --- no portability is involved.

Lastly, Sky Computing **benefits single-cloud users too** --- Sky can optimize across
a single cloud provider's regions/zones, pricing models, and hardware.

SkyPilot and Sky Computing
---------------------------------------------------

SkyPilot was born out of the same UC Berkeley lab (Sky Computing Lab) that
proposed the Sky Computing paradigm.

SkyPilot is Sky's first instantiation, focusing on AI and compute-intensive
workloads. It was started to showcase Sky Computing's benefits for these workloads.

Over the last few years, SkyPilot has grown past its initial academic
roots, and has now become widely adopted by AI teams in the industry. Today, it
is being used by ~10s to 100s of leading organizations. While the initial team
consisted of Berkeley PhDs and researchers, the SkyPilot community has now grown
to ~100 open-source contributors.

SkyPilot operates in a BYOC (Bring Your Own Cloud) model, where all resources
are launched in your existing cloud accounts, VPCs, and clusters.

SkyPilot is open sourced under the permissive Apache 2 license. It is under
active development on `GitHub <https://github.com/skypilot-org/skypilot>`_.

.. Why do AI and compute-intensive workloads benefit from Sky Computing?
Why does AI benefit from Sky Computing?
---------------------------------------------------

TODO: Convincing arguments here. Tone of 'talking to a new hire'.

What about data?
---------------------------------------------------

TODO: Talk about data locality.

What if I have a single cloud?
---------------------------------------------------

Just like autodriving has different levels of autonomy (e.g., Level 1-5), one can adopt Sky Computing in increasing levels.

**For users on a fixed cluster** (e.g., Kubernetes, Slurm), SkyPilot provides:

- A simple interface to submit and manage AI workloads, tailored to AI users' ergonomics.
- Support for dev clusters, jobs, and serving on your cluster.
- Cost savings via autostop & better hardware utilization.
- Future-proofness: No retooling when you add other clusters or clouds in the future.

**For users running in one cloud region/zone**, SkyPilot provides:

- Auto-retry and auto-fallback provisioner: Specify many hardware fallback targets and SkyPilot will auto-optimize and auto-retry to combat GPU shortage.
- Battle-tested job recovery, including on spot instances.
- :ref:`Simple workload packaging <quickstart>`: Wrap your existing AI projects in a simple SkyPilot YAML and have all infra tasks handled for you.
- Plus all of the benefits above.

**For users running on one cloud's multi-region**, SkyPilot provides:

- Support for a single job to utilize multiple regions for GPU availability & faster recovery.
- Support for a model's replicas to span multiple regions for availability & cost savings.
- Plus all of the benefits above.

**For users running on multiple clouds or clusters**, SkyPilot

- Combines all of your infra into a unified pool (*Sky Computing*), for higher productivity, cost savings, and capacity.
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


Getting involved
---------------------------------------------------

**Try out SkyPilot**: Try out SkyPilot in 5 minutes via the :ref:`quickstart guide <quickstart>`.

**Share your feedback**: Chat with the team on `SkyPilot Slack <http://slack.skypilot.co>`_ or drop a note on our `GitHub <https://github.com/skypilot-org/skypilot>`_.

**Contributing**: We welcome contributions from the community! See `CONTRIBUTING <https://github.com/skypilot-org/skypilot/blob/master/CONTRIBUTING.md>`_.
