Welcome to SkyPilot!
====================

.. image:: /_static/SkyPilot_wide_dark.svg
  :width: 50%
  :align: center
  :alt: SkyPilot
  :class: no-scaled-link, only-dark
.. image:: /_static/SkyPilot_wide_light.svg
  :width: 50%
  :align: center
  :alt: SkyPilot
  :class: no-scaled-link, only-light

.. raw:: html

   <p></p>
   <p style="text-align:center">
   <strong>Run AI on Any Infra</strong> — Unified, Faster, Cheaper
   </p>
   <p style="text-align:center">
   <a class="github-button" href="https://github.com/skypilot-org/skypilot" data-show-count="true" data-size="large" aria-label="Star skypilot-org/skypilot on GitHub">Star</a>
   <a class="github-button" href="https://github.com/skypilot-org/skypilot/subscription" data-icon="octicon-eye" data-size="large" aria-label="Watch skypilot-org/skypilot on GitHub">Watch</a>
   <a class="reference external image-reference" style="vertical-align:9.5px" href="http://slack.skypilot.co"><img src="https://img.shields.io/badge/SkyPilot-Join%20Slack-blue?logo=slack" style="height:27px"></a>
   <script async defer src="https://buttons.github.io/buttons.js"></script>
   </p>

SkyPilot is an open-source framework for running AI and batch workloads on any infra.


SkyPilot **is easy to use for AI users**:

- Quickly spin up compute on your own infra
- Environment and job as code --- simple and portable
- Easy job management: queue, run, and auto-recover many jobs

SkyPilot **unifies multiple clusters, clouds, and hardware**:

- One interface to use reserved GPUs, Kubernetes clusters, or 16+ clouds
- :ref:`Flexible provisioning <auto-failover>` of GPUs, TPUs, CPUs, with smart failover
- :ref:`Team deployment <sky-api-server>` and resource sharing

SkyPilot **cuts your cloud costs & maximizes GPU availability**:

* Autostop: automatic cleanup of idle resources
* :ref:`Spot instance support <spot-jobs>`: 3-6x cost savings, with preemption auto-recovery
* Intelligent scheduling: automatically run on the cheapest & most available infra

.. raw:: html

   <script>
   // Track the timeout to be able to clear it later
   var replayTimeout;
   var isPaused = false;
   var isEnded = false;
   
   function pauseAndReplay(video) {
     // Clear any existing timeout first
     clearTimeout(replayTimeout);
     
     // Mark the video as ended
     isEnded = true;
     // Update the pause button to show replay
     updatePauseButton();
     
     replayTimeout = setTimeout(function() {
        replayVideo(video);
     }, 10000); // 10 second gap
   }

   function replayVideo(video) {
      // Clear any pending auto-replay timeouts
      clearTimeout(replayTimeout);
      video.currentTime = 0;
      video.play();
      isEnded = false;
      isPaused = false;
      updatePauseButton();
   }

   function restartVideo(video) {
      // Clear any pending auto-replay timeouts when manually restarting
      clearTimeout(replayTimeout);
      video.currentTime = 0;
      video.play();
      isEnded = false;
      isPaused = false;
      updatePauseButton();
   }
   
   function togglePlayPause(video) {
      if (isEnded) {
         // If video has ended, replay it
         replayVideo(video);
      } else if (video.paused) {
         // If video is paused, play it
         video.play();
         isPaused = false;
         updatePauseButton();
      } else {
         // If video is playing, pause it
         video.pause();
         isPaused = true;
         // Clear timeout when paused
         clearTimeout(replayTimeout);
         updatePauseButton();
      }
   }
   
   function updatePauseButton() {
      var pauseBtn = document.getElementById('pause-btn');
      if (isEnded) {
         pauseBtn.innerHTML = "↻";
         pauseBtn.title = "Replay";
         pauseBtn.setAttribute('data-tooltip', 'Replay');
      } else if (isPaused) {
         pauseBtn.innerHTML = "▶";
         pauseBtn.title = "Play";
         pauseBtn.setAttribute('data-tooltip', 'Resume');
      } else {
         pauseBtn.innerHTML = "⏸︎";
         pauseBtn.title = "Pause";
         pauseBtn.setAttribute('data-tooltip', 'Pause');
      }
   }
   </script>
   <style>
     .video-control-btn {
       position: absolute;
       top: 10px;
       right: 10px;
       width: 32px;
       height: 32px;
       display: flex;
       align-items: center;
       justify-content: center;
       background-color: transparent;
       color: white;
       border: none;
       cursor: pointer;
       opacity: 0.7;
       transition: opacity 0.3s;
       font-size: 18px;
     }
     
     .video-control-btn:hover {
       opacity: 1;
     }
     
     .video-control-btn::after {
       content: attr(data-tooltip);
       position: absolute;
       bottom: -35px;
       right: 0;
       background-color: rgba(0, 0, 0, 0.7);
       color: white;
       padding: 5px 10px;
       border-radius: 4px;
       font-size: 14px;
       white-space: nowrap;
       opacity: 0;
       visibility: hidden;
       transition: opacity 0.3s;
     }
     
     .video-control-btn:hover::after {
       opacity: 1;
       visibility: visible;
     }
   </style>
   <div style="position: relative; margin-bottom: 20px;">
     <video id="video-with-badge" style="width: 100%; height: auto;" autoplay muted playsinline onended="pauseAndReplay(this)">
        <source src="../_static/intro.mp4" type="video/mp4" />
     </video>
     <button id="pause-btn" class="video-control-btn" onclick="togglePlayPause(document.getElementById('video-with-badge'))" title="Pause" data-tooltip="Pause">⏸︎</button>
   </div>


SkyPilot supports your existing GPU, TPU, and CPU workloads, with no code changes.



Current supported infra: Kubernetes, AWS, GCP, Azure, OCI, Lambda Cloud, Fluidstack,
RunPod, Cudo, Digital Ocean, Paperspace, Cloudflare, Samsung, IBM, Vast.ai,
VMware vSphere, Nebius.

.. raw:: html

   <p align="center">
   <picture>
      <img class="only-light" alt="SkyPilot Supported Clouds" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-light.png" width=85%>
      <img class="only-dark" alt="SkyPilot Supported Clouds" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-dark.png" width=85%>
   </picture>
   </p>

Ready to get started?
----------------------

:ref:`Install SkyPilot <installation>` in 1 minute. Then, launch your first cluster in 2 minutes in :ref:`Quickstart <quickstart>`.

SkyPilot is BYOC: Everything is launched within your cloud accounts, VPCs, and clusters.

Contact the SkyPilot team
---------------------------------

You can chat with the SkyPilot team and community on the `SkyPilot Slack <http://slack.skypilot.co>`_.

Learn more
--------------------------

To learn more, see :ref:`SkyPilot Overview <overview>` and `SkyPilot blog <https://blog.skypilot.co/>`_.

Case Studies and integrations: `Community Spotlights <https://blog.skypilot.co/community/>`_

Follow updates:

* `Slack <http://slack.skypilot.co>`_
* `X / Twitter <https://twitter.com/skypilot_org>`_
* `LinkedIn <https://www.linkedin.com/company/skypilot-oss/>`_
* `SkyPilot Blog <https://blog.skypilot.co/>`_ (`Introductory blog post <https://blog.skypilot.co/introducing-skypilot/>`_)

Read the research:

* `SkyPilot paper <https://www.usenix.org/system/files/nsdi23-yang-zongheng.pdf>`_ and `talk <https://www.usenix.org/conference/nsdi23/presentation/yang-zongheng>`_ (NSDI 2023)
* `Sky Computing whitepaper <https://arxiv.org/abs/2205.07147>`_
* `Sky Computing vision paper <https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s02-stoica.pdf>`_ (HotOS 2021)
* `SkyServe: AI serving across regions and clouds <https://arxiv.org/pdf/2411.01438>`_ (EuroSys 2025)
* `Managed jobs spot instance policy <https://www.usenix.org/conference/nsdi24/presentation/wu-zhanghao>`_ (NSDI 2024)

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Getting Started

   ../overview
   ../getting-started/installation
   ../getting-started/quickstart
   ../examples/index
   ../sky-computing

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Clusters

   ../examples/interactive-development
   Cluster Jobs <../reference/job-queue>
   ../examples/auto-failover
   ../reference/auto-stop

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Jobs

   ../examples/managed-jobs
   Multi-Node Jobs <../running-jobs/distributed-jobs>
   Many Parallel Jobs <../running-jobs/many-jobs>
   Model Training Guide <../reference/training-guide>

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Model Serving

   Getting Started <../serving/sky-serve>
   ../serving/user-guides

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Infra Choices

   ../reference/kubernetes/index
   Using Existing Machines <../reservations/existing-machines>
   ../reservations/reservations
   Using Cloud VMs <../compute/cloud-vm>
   ../compute/gpus



.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Data

   ../reference/storage
   ../examples/syncing-code-artifacts

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: User Guides

   ../reference/async
   Secrets and Environment Variables <../running-jobs/environment-variables>
   Docker Containers <../examples/docker-containers>
   ../examples/ports
   ../reference/logging
   ../reference/faq

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Administrator Guides

   ../reference/api-server/api-server
   ../cloud-setup/cloud-permissions/index
   ../cloud-setup/quota
   Admin Policies <../cloud-setup/policy>

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: References

   Task YAML <../reference/yaml-spec>
   CLI <../reference/cli>
   ../reference/api
   ../reference/config
   ../developers/index

