.. _skypilot-sandboxes:

.. rst-class:: hero-title

Sandboxes
=========

SkyPilot Sandboxes are fast, isolated compute environments that run on your own
Kubernetes clusters. Each sandbox is a lightweight pod you can launch on demand,
run commands in, and tear down, without provisioning a full cluster.

Sandboxes are built for **AI coding agents**, **RL training rollouts**, and
**parallel evals**: workloads that need many short-lived, isolated environments
spun up and down quickly. Pre-warmed pools launch sandboxes in **under a
second**, with volumes and secrets injected automatically. A built-in image for
`Claude Code <https://www.anthropic.com/claude-code>`_ ships out of the box.

.. tip::

   Sandboxes are part of **SkyPilot Platform**, in limited early access.
   `Sign up here <https://forms.gle/o4keAryXsVazNjyGA>`_; takes 20 seconds.

.. raw:: html

   <figure class="align-center" style="width: 90%; margin: 0 auto 20px auto;">
     <video id="sandbox-video" style="width: 100%; height: auto;" autoplay muted playsinline loop>
        <source src="_static/sandbox-claude.mp4" type="video/mp4" />
     </video>
     <figcaption><p>Sandboxes: launching a Claude Code environment on your own cluster with secrets injected from the SkyPilot Secrets Manager.</p></figcaption>
   </figure>

Why sandboxes with SkyPilot
---------------------------

* **Sub-second launches:** pre-warmed pools keep idle environments ready, so a
  sandbox is live in under a second instead of waiting on image pulls and
  scheduling.
* **Isolated per pod:** every sandbox is its own Kubernetes pod with a dedicated
  image, CPU, and memory, a natural boundary for running agent-generated or
  otherwise untrusted code without it touching your other workloads.
* **Secrets stay out of your code:** credentials are injected at launch from the
  SkyPilot Secrets Manager as environment variables, so tokens are never baked
  into images or hardcoded into the commands an agent runs.
* **Massively parallel:** launch thousands of sandboxes in one call for RL
  rollouts and parallel evals, then fan out commands concurrently.
* **Runs on your infra:** sandboxes live on your own Kubernetes clusters, so your
  code and data never leave your environment, and capacity is simply your
  existing cluster.

Use cases
---------

* **AI coding agents:** give each agent (e.g. Claude Code) its own disposable
  environment to read, write, and run code in, isolated from your other work and
  from other agents.
* **RL training rollouts:** spin up thousands of sandboxes to run rollouts in
  parallel, collect results, and tear them down, all from a single process.
* **Parallel evals:** run a large eval suite across many isolated environments at
  once instead of serializing on one machine.
* **Ephemeral build and CI tasks:** execute short-lived builds, tests, or scripts
  in a clean environment without provisioning a full cluster.

Quickstart
----------

A sandbox is a pod you create, run commands in, and terminate. Once the SkyPilot
CLI and the bundled Sandbox SDK are installed, that is the whole loop:

.. code-block:: python

   import sky.sandbox

   # Create a sandbox from the built-in `default` pool.
   sb = sky.sandbox.create(name='dev')

   # Run a command (argv tokens, no implicit shell); get
   # stdout / stderr / exit_code back.
   result = sb.exec('python', '-c', 'print(2 ** 10)')
   print(result['stdout'])  # 1024

   # Tear it down.
   sb.terminate()

Commands are argv tokens run directly in the pod (no implicit shell). For shell
features like pipes, globs, or env-var expansion, invoke a shell explicitly:
``sb.exec('sh', '-c', 'echo $HOME | wc -c')``.

Or use the context manager to terminate automatically:

.. code-block:: python

   import sky.sandbox

   with sky.sandbox.create(name='dev') as sb:
       sb.exec('python', 'train.py')
   # Sandbox is terminated on exit.

Working with the SDK
--------------------

Beyond create / exec / terminate, the ``sky.sandbox`` SDK covers batch and async
fan-out, and secret / volume injection:

.. tab-set::

    .. tab-item:: Batch

        Pass ``num_sandboxes`` to create a batch in one call; names are
        auto-generated from the prefix (``rollout-0001``, ``rollout-0002``, ...).

        .. code-block:: python

            import sky.sandbox

            sandboxes = sky.sandbox.create(name='rollout', num_sandboxes=1000)
            for i, sb in enumerate(sandboxes):
                sb.exec('python', 'rollout.py', str(i))

    .. tab-item:: Async fan-out

        Every entrypoint has an async sibling on a ``.aio`` attribute. A single
        event loop can drive hundreds of concurrent ``exec`` calls.

        .. code-block:: python

            import asyncio
            import sky.sandbox

            async def main():
                sandboxes = await sky.sandbox.create.aio(
                    name='rollout', num_sandboxes=100)
                try:
                    results = await asyncio.gather(
                        *(sb.exec.aio('python', 'rollout.py', str(i))
                          for i, sb in enumerate(sandboxes)))
                finally:
                    # Always tear down, even if an exec raises.
                    await asyncio.gather(*(sb.terminate.aio() for sb in sandboxes))
                    await sky.sandbox.aclose()  # release the shared session

            asyncio.run(main())

    .. tab-item:: Secrets & volumes

        Inject secrets and mount volumes at create time.

        .. code-block:: python

            import sky.sandbox

            sb = sky.sandbox.create(
                name='job',
                # Inject secrets from the secrets manager as env vars of the
                # same name.
                secrets=['HF_TOKEN'],
                # Plain (non-secret) env vars.
                env={'PROJECT': 'demo'},
                # Mount existing volumes, keyed by mount path (create them with
                # `sky volumes apply`).
                volumes={'/data': 'shared-data'},
            )

The SDK exposes ``create``, ``exec``, ``terminate``, ``ls``, ``create_pool``,
``set_pool_size``, and ``delete_pool``. Every per-call entrypoint has an async
sibling on a ``.aio`` attribute (``sky.sandbox.create.aio(...)``,
``sb.exec.aio(...)``), so the same names work in event-loop code. See the
dashboard's **Sandboxes** page to manage pools and running sandboxes in the UI.

Running AI coding agents
------------------------

The built-in ``claude`` pool ships with `Claude Code
<https://www.anthropic.com/claude-code>`_ installed. Give an agent its own
isolated sandbox, inject its token from the secrets manager, and drive it
non-interactively:

.. code-block:: python

   import sky.sandbox

   with sky.sandbox.create(
       name='claude',
       pool='claude',
       secrets=['CLAUDE_CODE_OAUTH_TOKEN'],
   ) as sb:
       # Shell features like pipes need an explicit shell.
       result = sb.exec(
           'sh', '-c',
           "echo 'Create a hello world index.html' | "
           'claude -p --dangerously-skip-permissions')
       print(result['stdout'])

Each agent runs in its own pod, so many agents can work in parallel without
sharing a filesystem or stepping on each other.

Pools
-----

A **pool** defines the shape of a sandbox (its container image, CPU, and memory)
and keeps a set of warm, pre-provisioned pods ready. Launching is instant:
``create`` *claims* a ready pod from the pool, which becomes your running
sandbox. SkyPilot ships a built-in ``default`` pool (a ``python`` image), so the
quickstart above needs no setup; create your own when you need a different image
or size.

.. raw:: html

   <iframe id="sandbox-pools-diagram"
           src="_static/sandboxes-diagram.html"
           title="How sandbox pools work"
           loading="lazy"
           scrolling="no"
           style="width: 100%; max-width: 1000px; height: 470px; border: none; background: transparent; display: block; margin: 0 auto 24px auto;"></iframe>
   <script>
   // The standalone diagram draws its own white card frame and a gray
   // dotted-grid stage. Embedded in the docs those read as a floating
   // gray/white box, so strip just those two layers (keeping the inner
   // cards) to sit the diagram directly on the page. Selector-based
   // !important rules beat the diagram's runtime inline styles and survive
   // its re-renders; keeping this here (not in the exported asset) means it
   // persists across diagram re-exports.
   (function () {
     var iframe = document.getElementById('sandbox-pools-diagram');
     if (!iframe) return;
     function strip() {
       try {
         var doc = iframe.contentDocument;
         if (!doc || !doc.head || doc.getElementById('sky-embed-transparent')) return;
         var s = doc.createElement('style');
         s.id = 'sky-embed-transparent';
         s.textContent =
           'div[style*="margin: 0px auto"][style*="border-radius: 16px"]{background:transparent !important;border:none !important;box-shadow:none !important;}' +
           'div[style*="margin: 0px auto"][style*="border-radius: 16px"] > div{background:transparent !important;}' +
           'div[style*="radial-gradient"]{background:transparent !important;background-image:none !important;}';
         doc.head.appendChild(s);
       } catch (e) { /* cross-document access can fail harmlessly; ignore */ }
     }
     iframe.addEventListener('load', strip);
     strip();
   })();
   </script>

Create a pool, and resize it at any time:

.. code-block:: python

   import sky.sandbox

   # Create a pool with 10 warm pods kept idle and ready.
   sky.sandbox.create_pool(
       name='ml-gpu',
       image='nvcr.io/nvidia/pytorch:24.05-py3',
       cpus=8,
       memory_gb=64,
       replicas=10,
   )

   # Scale the pool up or down at any time.
   sky.sandbox.set_pool_size('ml-gpu', replicas=50)

   # Launch a sandbox from the pool.
   sb = sky.sandbox.create(name='train', pool='ml-gpu')

.. seealso::

   - :ref:`volumes-all`: persistent storage you can mount into sandboxes.
   - :ref:`job-groups`: run many parallel jobs and sandboxes together for RL.
   - :ref:`skypilot-frontier-ai`: SkyPilot Platform, including the Secrets
     Manager that injects credentials into sandboxes.

.. tip::

   Sandboxes are part of **SkyPilot Platform**, in limited early access.
   `Sign up here <https://forms.gle/o4keAryXsVazNjyGA>`_; takes 20 seconds.
