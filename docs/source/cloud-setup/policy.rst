.. _advanced-policy-config:

Admin Policy Enforcement
========================


SkyPilot allows admins to enforce policies on users' SkyPilot usage by applying
custom validation and mutation logic on user's task and SkyPilot config.

In short, admins offers a Python package with a customized inheritance of SkyPilot's
``AdminPolicy`` interface, and a user just needs to set the ``admin_policy`` field in
the SkyPilot config ``~/.sky/config.yaml`` to enforce the policy to all their
tasks.

Overview
--------



User-Side
~~~~~~~~~~

To apply the policy, a user needs to set the ``admin_policy`` field in the SkyPilot config
``~/.sky/config.yaml`` to the path of the Python package that implements the policy.
For example:

.. code-block:: yaml

    admin_policy: mypackage.subpackage.MyPolicy


.. hint::

    SkyPilot loads the policy from the given package in the same Python environment.
    You can test the existance of the policy by running:

    .. code-block:: bash

        python -c "from mypackage.subpackage import MyPolicy"


Admin-Side
~~~~~~~~~~

An admin can distribute the Python package to users with pre-defined policy. The
policy should follow the following interface:

.. code-block:: python

    import sky

    class MyPolicy(sky.AdminPolicy):
        @classmethod
        def validate_and_mutate(cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
            # Logics for validate and modify user requests.
            ...
            return sky.MutatedUserRequest(user_request.task,
                                          user_request.skypilot_config)


``UserRequest`` and ``MutatedUserRequest`` are defined as follows:

.. code-block:: python

    class UserRequest:
        task: sky.Task
        skypilot_config: sky.NestedConfig
        # More fields can be added in the future.

    class MutatedUserRequest:
        task: sky.Task
        skypilot_config: sky.NestedConfig

That said, an ``AdminPolicy`` can mutate any fields of a user request, including
the :ref:`task <yaml-spec>` and the :ref:`global skypilot config <config-yaml>`,
giving admins a lot of flexibility to control user's SkyPilot usage.

An ``AdminPolicy`` is responsible to both validate and mutate user requests. If
a request should be rejected, the policy should raise an exception.


Example Policies    
----------------

Reject All
~~~~~~~~~~

.. code-block:: python

    class RejectAllPolicy(sky.AdminPolicy):
        @classmethod
        def validate_and_mutate(cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
            raise RuntimeError("This policy rejects all user requests.")

.. code-block:: yaml

    admin_policy: examples.admin_policy.reject_all.RejectAllPolicy


Add Kubernetes Labels for all Tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    class AddLabelsPolicy(sky.AdminPolicy):
        @classmethod
        def validate_and_mutate(cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:            
            config = user_request.skypilot_config
            labels = config.get_nested(('kubernetes', 'labels'), {})
            labels['app'] = 'skypilot'
            config.set_nested(('kubernetes', 'labels'), labels)
            return sky.MutatedUserRequest(user_request.task, config)

.. code-block:: yaml

    admin_policy: examples.admin_policy.add_labels.AddLabelsPolicy


Always Disable Public IP for AWS Tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    class DisablePublicIPPolicy(sky.AdminPolicy):
        @classmethod
        def validate_and_mutate(cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
            config = user_request.skypilot_config
            config.set_nested(('aws', 'use_internal_ip'), True)
            if config.get_nested(('aws', 'vpc_name'), None) is None:
                # If no VPC name is specified, it is likely a mistake. We should
                # reject the request
                raise RuntimeError('VPC name should be set. Check organization '
                                   'wiki for more information.')
            return sky.MutatedUserRequest(user_request.task, config)

.. code-block:: yaml

    admin_policy: examples.admin_policy.disable_public_ip.DisablePublicIPPolicy
