Iteratively Developing a Project
====================================

In this example we will do a walkthrough of a typical workflow for iteratively
developing a machine learning project on Sky.

Provisioning a VM
------------------
To provision a virtual machine named dev, run the following command:

.. code-block:: console

  $ sky gpunode -c dev

By default, GPU nodes are provisioned on the cheapest cloud with a single K80 GPU.
If you prefer, you can review our CLI reference for all configuration options.

Development
------------
To log in to the machine, Sky provides easy password-less SSH access:

.. code-block:: console

  $ ssh dev

This also allows for integration with common tools such as :code:`scp`, :code:`rsync`, and
VSCode Remote.

Running your code
--------------------
You can either run your project by directly logging into the VM and running shell commands, but Sky also
provides remote execution without logging in:

.. code-block:: bash

  # run a command without logging in
  # this stays within the Sky framework and allows us
  # to provide other features such as job queueing and monitoring
  $ sky exec dev -- python train.py

  # if you have a task.yaml, you can also directly execute the `run` section
  # defined in the task specification.
  $ sky exec dev task.yaml

Saving code and transferring artifacts
--------------------------------------
Sky automatically forwards your local SSH agent to the cluster, allowing for git access
without the need to log in again. For more information on how to configure your local SSH agent
for forwarding git credentials, please refer to `GitHub's tutorial <https://code.visualstudio.com/docs/remote/remote-overview>`_.

Additionally, you can always use the following to transfer files between your local machine and remote VM:

.. code-block::

    $ scp -r my_code/ dev:/path/to/destination  # copy files to remote VM
    $ scp -r dev:/path/to/source my_code/       # copy files from remote VM

Sky also provides automatic transfer of working directory to the remote VM. This
can be configured with the :code:`workdir` option in a task YAML file, or using the following
command line options:

.. code-block::

    $ sky exec --workdir=/path/to/code task.yaml
    $ sky launch --workdir=/path/to/code task.yaml

In both scenarios, the working directory is transferred to the remote VM and will override
:code:`workdir` defaults specified in task.yaml

Ending a development session
-----------------------------
To end a development session, run the following command:

.. code-block:: console

  $ sky stop dev

To restart the VM:

.. code-block:: console

    $ sky start dev