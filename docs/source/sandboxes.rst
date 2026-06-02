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
   `Sign up here <https://forms.gle/HGGMjzvRz8Mqn9pn7>`_; takes 20 seconds.

.. raw:: html

   <figure class="align-center" style="width: 90%; margin: 0 auto 20px auto;">
     <video id="sandbox-video" style="width: 100%; height: auto;" autoplay muted playsinline loop>
        <source src="_static/sandbox-claude.mp4" type="video/mp4" />
     </video>
     <figcaption><p>Sandboxes: launching a Claude Code environment on your own cluster with secrets injected from the SkyPilot Secrets Manager.</p></figcaption>
   </figure>

Why sandboxes?
--------------

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

How it works
------------

A **template** defines the shape of a sandbox: its container image, CPU, memory,
and any baked-in volumes. Each template is backed by a **warm pool** of idle
pods, so launches against it are instant. SkyPilot ships a built-in ``default``
template (a ``python`` image); create your own when you need a different image or
size.

A **sandbox** is a single running pod claimed from a template's warm pool. It has
a unique name, a lifecycle you control (create, use, then terminate), and is
accessible until you call ``terminate()``. Commands are passed as argv tokens and
run directly via the same exec primitive ``kubectl exec`` uses (no implicit
shell), returning ``stdout``, ``stderr``, and the exit code synchronously.

Because each sandbox is its own pod, it is isolated from other sandboxes and
workloads on the cluster, a clean boundary for code an agent generates at
runtime. Secrets referenced at launch are resolved by the SkyPilot Secrets
Manager and injected as environment variables, so they are never stored in the
template image or passed on the command line.

Quickstart
----------

Once the SkyPilot CLI and the bundled Sandbox SDK are installed, create a
sandbox, run a command, and terminate it:

.. code-block:: python

   import sky.sandbox

   # Create a sandbox from the built-in `default` template.
   sb = sky.sandbox.create(name='dev')

   # Run a command (argv tokens, no implicit shell); get
   # stdout / stderr / exit_code back.
   result = sb.exec('python', '-c', 'print(2 ** 10)')
   print(result['stdout'])  # 1024

   # Tear it down.
   sb.terminate()

Or use the context manager to terminate automatically:

.. code-block:: python

   import sky.sandbox

   with sky.sandbox.create(name='dev') as sb:
       sb.exec('python', 'train.py')
   # Sandbox is terminated on exit.

Working with the SDK
--------------------

Beyond create / exec / terminate, the ``sky.sandbox`` SDK covers batch and async
fan-out, templates and warm pools, and secret / volume injection:

.. tab-set::

    .. tab-item:: Batch

        Pass ``num_sandboxes`` to create a batch in one call; names are
        auto-generated from the prefix (``rollout-0001``, ``rollout-0002``, ...).

        .. code-block:: python

            import sky.sandbox

            sandboxes = sky.sandbox.create(
                name='rollout',
                template='ml-gpu',
                num_sandboxes=1000,
            )
            for sb in sandboxes:
                sb.exec('python', 'rollout.py')

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
                        *(sb.exec.aio('python', 'rollout.py') for sb in sandboxes))
                finally:
                    # Always tear down, even if an exec raises.
                    await asyncio.gather(*(sb.terminate.aio() for sb in sandboxes))
                    await sky.sandbox.aclose()  # release the shared session

            asyncio.run(main())

    .. tab-item:: Templates & warm pools

        Define a reusable template and keep a warm pool ready for instant
        launches.

        .. code-block:: python

            import sky.sandbox

            # Create a template with 10 warm pods kept idle and ready.
            sky.sandbox.create_template(
                name='ml-gpu',
                image='nvcr.io/nvidia/pytorch:24.05-py3',
                cpus=8,
                memory_gb=64,
                pool_replicas=10,
            )

            # Scale the warm pool up or down at any time.
            sky.sandbox.set_pool_size('ml-gpu', replicas=50)

    .. tab-item:: Secrets & volumes

        Inject secrets and mount volumes at create time.

        .. code-block:: python

            import sky.sandbox

            sb = sky.sandbox.create(
                name='job',
                template='ml-gpu',
                # Inject secrets from the secrets manager as env vars of the
                # same name.
                secrets=['HF_TOKEN'],
                # Plain (non-secret) env vars.
                env={'PROJECT': 'demo'},
                # Mount existing volumes, keyed by mount path (create them with
                # `sky volumes apply`).
                volumes={'/data': 'shared-data'},
            )

    .. tab-item:: Claude Code

        The built-in ``claude`` template ships with Claude Code installed; pass
        an OAuth token via the secrets manager and drive it non-interactively.

        .. code-block:: python

            import sky.sandbox

            with sky.sandbox.create(
                name='claude',
                template='claude',
                secrets=['CLAUDE_CODE_OAUTH_TOKEN'],
            ) as sb:
                # Shell features like pipes need an explicit shell.
                result = sb.exec(
                    'sh', '-c',
                    "echo 'Create a hello world index.html' | "
                    'claude -p --dangerously-skip-permissions')
                print(result['stdout'])

The SDK exposes ``create``, ``exec``, ``terminate``, ``ls``, ``create_template``,
``set_pool_size``, and ``delete_template``. Every per-call entrypoint has an async
sibling on a ``.aio`` attribute (``sky.sandbox.create.aio(...)``,
``sb.exec.aio(...)``), so the same names work in event-loop code. See the
dashboard's **Sandboxes** page to manage templates, warm pools, and running
sandboxes in the UI.

.. seealso::

   - :ref:`volumes-all`: persistent storage you can mount into sandboxes.
   - :ref:`job-groups`: run many parallel jobs and sandboxes together for RL.
   - :ref:`skypilot-frontier-ai`: SkyPilot Platform, including the Secrets
     Manager that injects credentials into sandboxes.

.. tip::

   Sandboxes are part of **SkyPilot Platform**, in limited early access.
   `Sign up here <https://forms.gle/HGGMjzvRz8Mqn9pn7>`_; takes 20 seconds.
