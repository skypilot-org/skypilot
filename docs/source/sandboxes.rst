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
second**, with volumes and secrets injected automatically. Coding agents like
`Claude <https://www.anthropic.com/claude>`_ provision a sandbox through a tool
call and run their generated code in it, isolated from everything else.

.. tip::

   Sandboxes are part of **SkyPilot Platform**, in limited early access.
   `Sign up here <https://forms.gle/o4keAryXsVazNjyGA>`_; takes 20 seconds.

.. raw:: html

   <figure class="align-center" style="width: 90%; margin: 0 auto 20px auto;">
     <video id="sandbox-video" style="width: 100%; height: auto;" autoplay muted playsinline loop>
        <source src="_static/sandbox-chat.mp4" type="video/mp4" />
     </video>
     <figcaption><p>Sandboxes: a coding agent provisions a SkyPilot sandbox through a tool call and runs its code there, on your own cluster.</p></figcaption>
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
* **Snapshot & restore:** capture a sandbox's whole filesystem into a snapshot
  image and restore a fresh sandbox from it later, so expensive setup is done
  once and resumed instead of re-run.
* **Sandbox-to-sandbox networking:** sandboxes can't reach each other unless a
  sandbox explicitly exposes ports with ``ports=``, which peers then dial via
  stable endpoints that survive pod replacement.
* **Docker-in-Docker:** pass ``enable_docker=True`` to run ``docker build``,
  ``docker run``, and ``docker compose`` inside a sandbox, with any base image;
  the daemon runs in a privileged sidecar while the container your code runs in
  stays unprivileged.
* **Stronger isolation with gVisor:** on GKE, run sandbox pods on the gVisor
  runtime to add a kernel-level isolation boundary around untrusted code, on
  top of the per-pod isolation you get by default.

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

A sandbox is an isolated pod you create, run commands in, and terminate. Once
the SkyPilot CLI and the bundled Sandbox SDK are installed, that is the whole
loop:

.. tab-set::

    .. tab-item:: CLI

        ``sky sandbox create`` provisions a sandbox and drops you straight into
        an interactive shell; exit the shell and the sandbox is destroyed.

        .. code-block:: console

            # Create a sandbox and drop into a shell (destroyed on exit).
            $ sky sandbox create -n dev
            ✓ Sandbox dev is ready. Connecting via bash...

            # Or keep it running with --detach, then manage it by name.
            $ sky sandbox create --detach -n dev
            $ sky sandbox ls
            $ sky sandbox terminate dev

    .. tab-item:: Python

        .. code-block:: python

            import sky.sandbox

            # Create a sandbox from the built-in `default` pool.
            sb = sky.sandbox.create(name='dev')

            # Run a command (argv tokens, no implicit shell). exec returns a
            # handle: wait() for the exit code, then read stdout / stderr.
            proc = sb.exec('python', '-c', 'print(2 ** 10)')
            proc.wait()
            print(proc.stdout.read())  # 1024

            # Tear it down.
            sb.terminate()

        Commands are argv tokens run directly in the pod (no implicit shell).
        For shell features like pipes, globs, or env-var expansion, invoke a
        shell explicitly: ``sb.exec('sh', '-c', 'echo $HOME | wc -c')``.

        Pass ``env`` to set environment variables for a single command
        (overriding any create-time env, and not persisted to later execs):
        ``sb.exec('printenv', 'STAGE', env={'STAGE': 'ci'})``.

        Or use the context manager to terminate automatically:

        .. code-block:: python

            import sky.sandbox

            with sky.sandbox.create(name='dev') as sb:
                sb.exec('python', 'train.py').wait()
            # Sandbox is terminated on exit.

Working with the SDK
--------------------

Beyond create / exec / terminate, the ``sky.sandbox`` SDK covers batch and async
fan-out, secret / volume injection, sandbox-to-sandbox networking, and
filesystem snapshot & restore:

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
                # `sky volumes apply`). A value is a volume name (whole volume)
                # or a VolumeMount for a sub-directory / read-only mount.
                volumes={
                    '/data': 'shared-data',
                    '/work': sky.sandbox.VolumeMount('team-fs',
                                                     sub_path='job-123'),
                },
            )

    .. tab-item:: Sandbox networking

        Sandboxes can't reach each other by default. A *server* declares the
        ports it exposes with ``ports=``; each one shows up in its
        ``endpoints`` map as a stable address a *peer* can dial.

        .. code-block:: python

            import sky.sandbox

            # Server: expose port 8080 to peer sandboxes.
            server = sky.sandbox.create(name='api', image='python:3.12',
                                        ports=[8080])
            print(server.endpoints[8080])  # 'api.default.svc.cluster.local:8080'

            # Client: exposes nothing (so peers can't reach it), but can reach
            # the server on its exposed port.
            worker = sky.sandbox.create(name='worker', image='python:3.12')
            worker.exec('curl', '-sf',
                        f'http://{server.endpoints[8080]}/healthz')

        The address survives pod replacement, and un-exposed ports stay
        blocked on NetworkPolicy-enforcing clusters.

    .. tab-item:: Snapshot & restore

        Snapshot a sandbox's filesystem into a container image, then restore a
        fresh sandbox from it later: pause expensive setup and resume it
        without re-running it.

        .. code-block:: python

            import sky.sandbox

            # Set up a sandbox, then snapshot its filesystem.
            sb = sky.sandbox.create(name='dev', image='python:3.12')
            sb.exec('pip', 'install', 'numpy', 'pandas').wait()
            image = sb.snapshot()

            # Later: restore a fresh sandbox -- numpy/pandas already installed.
            restored = sky.sandbox.create(name='dev2', image=image)

        Requires a container registry configured in the dashboard's
        **Sandboxes** install dialog.

The SDK exposes ``create``, ``exec``, ``terminate``, ``snapshot``, ``ls``,
``create_pool``, ``set_pool_size``, and ``delete_pool``. Every per-call
entrypoint has an async sibling on a ``.aio`` attribute
(``sky.sandbox.create.aio(...)``, ``sb.exec.aio(...)``), so the same names work
in event-loop code. See the dashboard's **Sandboxes** page to manage pools and
running sandboxes in the UI.

Examples
--------

.. tab-set::

    .. tab-item:: RL training

        `RL code-execution training with sandbox rewards
        <https://github.com/skypilot-org/skypilot/tree/master/llm/rl-code-execution-sandbox>`_
        is a complete end-to-end example: a trainer fans out thousands of
        sandboxes to run untrusted model-generated code against hidden tests,
        scoring each rollout by whether its code passes. Every rollout is
        isolated in its own pod, so crashing, looping, or malicious code can't
        touch the trainer or other rollouts.

        The reward server claims a batch of sandboxes from a warm pool, scores
        each rollout concurrently, and tears them all down:

        .. code-block:: python

            async def score_batch(items):
                sandboxes = await sky.sandbox.create.aio(
                    name='reward', num_sandboxes=len(items), pool=POOL_NAME)
                try:
                    return await asyncio.gather(
                        *(score_one(sb, item)
                          for sb, item in zip(sandboxes, items)))
                finally:
                    await asyncio.gather(
                        *(sb.terminate.aio() for sb in sandboxes),
                        return_exceptions=True)

            async def score_one(sb, item):
                # Reward = did the model-generated code pass? exec returns a
                # handle; timeout_seconds bounds the command server-side (an
                # overrun is killed in the pod and reports a non-zero exit, so
                # it scores 0 without the client holding a long connection).
                proc = await sb.exec.aio('python', '-c', item.script,
                                         timeout_seconds=EXEC_TIMEOUT_SECONDS)
                code = await proc.wait()
                return 1.0 if code == 0 else 0.0

    .. tab-item:: AI coding agents

        The common pattern is **not** to run a coding agent *inside* a
        sandbox. Instead, the agent runs wherever your application does and is
        given a *tool* to provision a sandbox on demand and run its generated
        code there, isolated from everything else. Below, `Claude
        <https://www.anthropic.com/claude>`_ drives the ``sky.sandbox`` SDK
        through two tools: one to start a sandbox, one to run a command in it.

        .. code-block:: python

            import anthropic
            import sky.sandbox

            # Claude runs outside the sandbox and calls these tools to create
            # one on demand and run code in it. A chat that never runs code
            # never makes a pod.
            sb = None

            tools = [
                {'name': 'start_skypilot_sandbox',
                 'description': 'Provision a fresh sandbox. Call the first '
                                'time you need to run code.',
                 'input_schema': {'type': 'object', 'properties': {}}},
                {'name': 'run_shell',
                 'description': 'Run a shell command in the sandbox. State '
                                'persists across calls.',
                 'input_schema': {'type': 'object',
                                  'properties': {'command': {'type': 'string'}},
                                  'required': ['command']}},
            ]

            def dispatch(name, args):
                global sb
                if name == 'start_skypilot_sandbox':
                    # Claims a warm pod from the pool.
                    sb = sky.sandbox.create(name='chat')
                    return 'Sandbox ready.'
                # run_shell. exec returns a handle, so the connection is held
                # only for the launch + each poll, not for the command's whole
                # runtime: a long agent command (a build, a test suite, a
                # training run) completes instead of tripping an edge timeout.
                # timeout_seconds is the server-side budget after which the
                # command is killed in the pod -- size it to your longest tool
                # call rather than leaving the 60s default.
                proc = sb.exec('sh', '-c', args['command'],
                               timeout_seconds=3600)
                proc.wait()
                return proc.stdout.read() or proc.stderr.read()

            client = anthropic.Anthropic()
            messages = [
                {'role': 'user',
                 'content': 'Compute the 100th Fibonacci number with Python.'}]
            while True:
                resp = client.messages.create(model='claude-opus-4-8',
                                              max_tokens=4096, tools=tools,
                                              messages=messages)
                messages.append({'role': 'assistant', 'content': resp.content})
                tool_uses = [b for b in resp.content if b.type == 'tool_use']
                if not tool_uses:
                    break
                messages.append({'role': 'user', 'content': [
                    {'type': 'tool_result', 'tool_use_id': tu.id,
                     'content': dispatch(tu.name, tu.input)}
                    for tu in tool_uses]})

            if sb is not None:
                sb.terminate()

        Each agent gets its own pod, so many can work in parallel without
        sharing a filesystem or stepping on each other. Inject per-agent
        credentials at ``create`` time with ``secrets=[...]`` so tokens never
        appear in the commands the agent runs.

Advanced: Warm pools for fast provisioning
------------------------------------------

Without a pool, ``create`` provisions a fresh pod on demand, which waits on
Kubernetes scheduling and the container image pull. A **pool** keeps a set of
warm, pre-provisioned pods ready, so ``create`` instead *claims* an
already-running pod, cutting a single sandbox's launch time by more than 50%. A
pool also fixes the shape of its sandboxes: their container image, CPU, and
memory.

SkyPilot ships a built-in ``default`` pool (a ``python`` image), so the
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

Docker-in-Docker
----------------

Run ``docker build``, ``docker run``, and ``docker compose`` **inside** a
sandbox, with any base image. It is a one-flag opt-in: pass
``enable_docker=True``. The Docker daemon runs in a separate privileged
sidecar and the ``docker`` CLI is injected automatically, so the container
your code runs in stays unprivileged and your image needs no changes.

.. code-block:: python

   import sky.sandbox

   # Ad-hoc launch (fresh pod, cold start).
   sb = sky.sandbox.create(name='dev', image='ubuntu:22.04',
                           enable_docker=True)
   sb.exec('docker', 'run', '--rm', 'hello-world').wait()
   sb.exec('docker', 'build', '-t', 'app', '/src').wait()

   # Or bake Docker into a warm pool to keep sub-second launches.
   sky.sandbox.create_pool(name='docker-pool', image='ubuntu:22.04',
                           replicas=3, enable_docker=True)
   sky.sandbox.create(name='dev2', pool='docker-pool')

``enable_docker`` on ``create()`` applies to ad-hoc launches only (pass
``image=``); a warm pool's Docker support is fixed at pool creation, so pass
``enable_docker=True`` to ``create_pool`` instead.

.. note::

   The target cluster must admit privileged pods (for the Docker daemon
   sidecar). Clusters that enforce a no-privileged policy, such as GKE
   Autopilot, reject the launch.

Stronger isolation with gVisor on GKE
-------------------------------------

On GKE you can further harden sandbox pods by using `gVisor
<https://gvisor.dev/>`_. gVisor is officially supported by GKE, and in order to
make use of it through SkyPilot Sandboxes, the following two steps can be
performed:

1. **Create a GKE Sandbox node pool** with ``--sandbox type=gvisor``:

   .. code-block:: bash

      gcloud container node-pools create sandbox-pool \
        --cluster <cluster-name> \
        --sandbox type=gvisor

   Note that an existing node pool can't be converted to support gVisor, a new
   one must be created. Please see the `GKE Sandbox documentation
   <https://docs.cloud.google.com/kubernetes-engine/docs/how-to/sandbox-pods>`_
   for more details.

2. **Point sandboxes at that runtime class** by adding ``runtimeClassName:
   gvisor`` under ``kubernetes.pod_config`` in ``~/.sky/config.yaml``. SkyPilot
   uses this to ensure that sandboxes are created using gVisor:

   .. code-block:: yaml

      # ~/.sky/config.yaml
      kubernetes:
        pod_config:
          spec:
            runtimeClassName: gvisor

To enable gVisor for only some clusters, nest the same ``pod_config`` under
that context in ``kubernetes.context_configs.<context>`` instead:

.. code-block:: yaml

   # ~/.sky/config.yaml
   kubernetes:
     context_configs:
       gke_my-project_us-central1_my-cluster:
         pod_config:
           spec:
             runtimeClassName: gvisor

.. note::

   If you are using warm pools to speed up startup latencies of sandboxes and
   enable gVisor functionality after that, all subsequent sandbox launches
   from the warm pool will *not* respect using gVisor. Please re-create a warm
   pool for this to take effect. Snapshot & restore is currently not supported
   on the gVisor runtime.

.. seealso::

   - :ref:`volumes-all`: persistent storage you can mount into sandboxes.
   - :ref:`job-groups`: run many parallel jobs and sandboxes together for RL.
   - :ref:`skypilot-frontier-ai`: SkyPilot Platform, including the Secrets
     Manager that injects credentials into sandboxes.

.. tip::

   Sandboxes are part of **SkyPilot Platform**, in limited early access.
   `Sign up here <https://forms.gle/o4keAryXsVazNjyGA>`_; takes 20 seconds.
