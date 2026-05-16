.. _installation:

.. |community-badge| image:: https://img.shields.io/badge/Community%20Maintained-EAFAFF?style=flat
   :alt: Community Maintained

Installation
============

Install SkyPilot
--------------------------

.. tip::

   To use SkyPilot with AI agent (Claude Code, Codex, etc.), install :ref:`SkyPilot Skill <skill>` to give your agent full SkyPilot expertise.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # Create a virtual environment with pip pre-installed (required for SkyPilot)
      # SkyPilot requires 3.9 <= python <= 3.13.
      uv venv --seed --python 3.10

      source .venv/bin/activate
      uv pip install skypilot

      # Optional: to use SkyPilot locally, install it with dependencies of 15+ clouds.
      # See "Run as a standalone application locally" below.
      uv pip install "skypilot[kubernetes,aws,gcp]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # Install as a globally available tool with pip included
      # SkyPilot requires 3.9 <= python <= 3.13.
      uv tool install --with pip skypilot

      # Optional: to use SkyPilot locally, install it with dependencies of 15+ clouds.
      # See "Run as a standalone application locally" below.
      uv tool install --with pip "skypilot[kubernetes,aws,gcp]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # Recommended: use a new conda env to avoid package conflicts.
      # SkyPilot requires 3.7 <= python <= 3.13.
      conda create -y -n sky python=3.10
      conda activate sky

      pip install skypilot

      # Optional: to use SkyPilot locally, install it with dependencies of 15+ clouds.
      # See "Run as a standalone application locally" below.
      pip install "skypilot[kubernetes,aws,gcp]"



.. dropdown:: Install SkyPilot from nightly build or source

    SkyPilot provides nightly builds and source code for the latest features and for development.

    **Install from nightly build:**

    .. tab-set::
      .. tab-item:: uv venv
        :sync: uv-venv-tab

        .. code-block:: shell

          uv venv --seed --python 3.10
          source .venv/bin/activate

          uv pip install skypilot-nightly

      .. tab-item:: uv tool
        :sync: uv-tool-tab

        .. code-block:: shell

          uv tool install --with pip skypilot-nightly


      .. tab-item:: pip
        :sync: pip-tab

        .. code-block:: shell

          conda create -y -n sky python=3.10
          conda activate sky

          pip install skypilot-nightly

    **Install from source:**

    .. code-block:: shell

      uv venv --seed --python 3.10
      source .venv/bin/activate

      git clone https://github.com/skypilot-org/skypilot.git
      cd skypilot

      uv pip install -e .

      # Build the dashboard (requires Node.js and npm)
      npm --prefix sky/dashboard install
      npm --prefix sky/dashboard run build


.. _shell-completion:

.. dropdown:: Optional: Enable shell completion
   :animate: fade-in

   .. code-block:: shell

     # Replace 'auto' with 'zsh', 'bash', or 'fish' to target a specific shell
     sky --install-shell-completion auto

   To uninstall: ``sky --uninstall-shell-completion auto``

Alternatively, we also provide a :ref:`Docker image <docker-image>` as a quick way to try out SkyPilot.

Connect to a remote API server or run locally
------------------------------------------------------

SkyPilot can connect to a :ref:`remote API server <sky-api-server-remote>` for multi-user collaboration, or run as a :ref:`standalone application <sky-api-server-local>`

Connect to SkyPilot API server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If your team has set up a :ref:`SkyPilot remote API server <sky-api-server>`, connect to it by running:

.. code-block:: shell

  sky api login

Based on your API server setup, basic auth or SSO login is required to log into the API server. See :ref:`sky-api-server-connect` for more details.


Run as a standalone application locally
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Refer to the :ref:`credential setup page <cloud-account-setup>` to download the necessary dependencies and set up the credentials locally.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      uv pip install "skypilot[kubernetes,slurm,aws,gcp]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      uv tool install --with pip "skypilot[kubernetes,slurm,aws,gcp]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      pip install "skypilot[kubernetes,slurm,aws,gcp]"


.. _verify-cloud-access:

Verify cloud access
-----------------------------

After installation, run :code:`sky check` to verify that credentials are correctly set up:

.. code-block:: shell

  # Check specific clouds
  sky check aws gcp

  # Or check all clouds
  sky check

This will produce a summary like:

.. code-block:: text

  Checking credentials to enable clouds for SkyPilot.
    AWS: enabled
    GCP: enabled
    Azure: enabled
    OCI: enabled
    Lambda: enabled
    Nebius: enabled
    RunPod: enabled
    Paperspace: enabled
    Fluidstack: enabled
    Cudo: enabled
    Shadeform: enabled
    IBM: enabled
    SCP: enabled
    Seeweb: enabled
    vSphere: enabled
    Cloudflare (for R2 object store): enabled
    VastData: enabled
    DO: enabled
    Hyperbolic: enabled
    Mithril: enabled
    PrimeIntellect: enabled
    Vast: enabled
    Verda: enabled
    Yotta: enabled
    Kubernetes: enabled
    Slurm: enabled

If any cloud's credentials or dependencies are missing, ``sky check`` will
output hints on how to resolve them. You can also refer to the
:ref:`Setup credentials <cloud-account-setup>` page.

.. tip::

  If your clouds show ``enabled`` --- |:tada:| |:tada:| **Congratulations!** |:tada:| |:tada:| You can now head over to
  :ref:`Quickstart <quickstart>` to get started with SkyPilot.


.. toctree::
   :hidden:
   :maxdepth: 1

   cloud-account-setup

---------

.. _docker-image:

Appendix: Using SkyPilot in Docker
----------------------------------

A Docker image is available for quickly trying out SkyPilot:

.. code-block:: shell

  docker run --platform linux/amd64 \
    -td --name sky berkeleyskypilot/skypilot:latest
  docker exec -it sky /bin/bash

.. dropdown:: Mount local credentials and state into the container
   :animate: fade-in

   To reuse your existing cloud credentials and SkyPilot state:

   .. code-block:: shell

     docker run --platform linux/amd64 \
       -td --name sky \
       -v "$HOME/.sky:/root/.sky:rw" \
       -v "$HOME/.aws:/root/.aws:rw" \
       -v "$HOME/.config/gcloud:/root/.config/gcloud:rw" \
       berkeleyskypilot/skypilot:latest

After experimenting, clean up and stop the container:

.. code-block:: shell

  sky down -a -y && sky storage delete -a -y  # inside container
  docker stop sky


Troubleshooting
----------------

Why does ``sky launch`` fail with a ``pip`` or wheel build error?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SkyPilot needs ``pip`` at runtime to build wheels for remote cluster setup.
When using ``uv``, make sure to include the required flags:

.. code-block:: shell

  # For uv venv: --seed ensures pip is available in the environment
  uv venv --seed --python 3.10

  # For uv tool install: --with pip bundles pip into the tool environment
  uv tool install --with pip skypilot

Why are my changes or credentials not taking effect?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The local API server caches state at startup. Restart it after upgrading SkyPilot,
installing cloud dependencies, or configuring new credentials:

.. code-block:: shell

  sky api stop
  sky api start

See :ref:`upgrade-skypilot` for more details.

How do I run SkyPilot on Windows?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SkyPilot supports Windows through
`Windows Subsystem for Linux (WSL) <https://learn.microsoft.com/en-us/windows/wsl/install>`_.
SSH configs for SkyPilot clusters are automatically set up, and VS Code Remote-SSH
connection works out of the box on Windows.
