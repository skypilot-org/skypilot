.. _sky-faq:

Frequently Asked Questions
------------------------------------------------


.. contents::
    :local:
    :depth: 1


Can I clone private GitHub repositories in a task's ``setup`` commands?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes, provided you have `set up SSH agent forwarding <https://docs.github.com/en/developers/overview/using-ssh-agent-forwarding>`_.
For example, run the following on your laptop:

.. code-block:: bash

   eval $(ssh-agent -s)
   ssh-add ~/.ssh/id_rsa

Then, any Sky clusters launched from this machine would be able to clone private GitHub repositories. For example:

.. code-block:: yaml

    # your_task.yaml
    setup: |
      git clone git@github.com:your-proj/your-repo.git

Note: currently, cloning private repositories in the ``run`` commands is not supported yet.

How to mount additional files into a cloned repository?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you want to mount additional files into a path that will be ``git clone``-ed (either in ``setup`` or ``run``), cloning will fail and complain that the target path is not empty:

.. code-block:: yaml

  file_mounts:
    ~/code-repo/tmp.txt: ~/tmp.txt
  setup: |
    # Fail! Git will complain the target dir is not empty:
    #    fatal: destination path 'code-repo' already exists and is not an empty directory.
    # This is because file_mounts are processed before `setup`.
    git clone git@github.com:your-id/your-repo.git ~/code-repo/

To get around this, mount the files to a different path, then symlink to them.  For example:

.. code-block:: yaml

  file_mounts:
    /tmp/tmp.txt: ~/tmp.txt
  setup: |
    git clone git@github.com:your-id/your-repo.git ~/code-repo/
    ln -s /tmp/tmp.txt ~/code-repo/
