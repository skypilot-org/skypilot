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

Can I mount files in a repository cloned on remote in a task's ``setup`` / ``run`` section?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes, you can use symlink to link the mounted files/folders to the repository cloned in the ``setup`` / ``run`` section. For example:

.. code-block:: yaml

    # your_task.yaml
    file_mounts:
      /tmp/dst: /local/src

    setup: |
      git clone git@github.com:your-proj/your-repo.git
      ln -s /tmp/dst ./your-repo/dst
