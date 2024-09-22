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
        """User request to the policy.

        It is a combination of a task, request options, and the global skypilot
        config used to run a task, including `sky launch / exec / jobs launch / ..`.

        Args:
            task: User specified task.
            skypilot_config: Global skypilot config to be used in this request.
            request_options: Request options. It can be None for jobs and
                services.
        """
        task: sky.Task
        skypilot_config: sky.Config
        operation_args: sky.RequestOptions

    class MutatedUserRequest:
        task: sky.Task
        skypilot_config: sky.Config

That said, an ``AdminPolicy`` can mutate any fields of a user request, including
the :ref:`task <yaml-spec>` and the :ref:`global skypilot config <config-yaml>`,
giving admins a lot of flexibility to control user's SkyPilot usage.

An ``AdminPolicy`` is responsible to both validate and mutate user requests. If
a request should be rejected, the policy should raise an exception.

The ``sky.Config`` and ``sky.RequestOptions`` are defined as follows:

.. code-block:: python

    class Config:
        def get_nested(self,
                       keys: Tuple[str, ...],
                       default_value: Any,
                       override_configs: Optional[Dict[str, Any]] = None,
            ) -> Any:
            """Gets a value with nested keys.
            
            If override_configs is provided, it value will be merged on top of
            the current config.
            """
            ...

        def set_nested(self, keys: Tuple[str, ...], value: Any) -> None:
            """Sets a value with nested keys."""
            ...

    @dataclass
    class RequestOptions:
        """Options a user specified in their request to SkyPilot."""
        cluster_name: Optional[str]
        cluster_exists: bool
        idle_minutes_to_autostop: Optional[int]
        down: bool
        dryrun: bool


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


Enforce Autostop for all Tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    class EnforceAutostopPolicy(sky.AdminPolicy):
        @classmethod
        def validate_and_mutate(cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
            operation_args = user_request.operation_args
            # Operation args can be None for jobs and services, for which we
            # don't need to enforce autostop, as they are already managed.
            if operation_args is None:
                return sky.MutatedUserRequest(
                    task=user_request.task,
                    skypilot_config=user_request.skypilot_config)
            idle_minutes_to_autostop = operation_args.idle_minutes_to_autostop
            # Enforce autostop/down to be set for all tasks for new clusters.
            if not operation_args.cluster_exists and (
                    idle_minutes_to_autostop is None or
                    idle_minutes_to_autostop < 0):
                raise RuntimeError('Autostop/down must be set for all newly '
                                'launched clusters.')
            return sky.MutatedUserRequest(
                task=user_request.task,
                skypilot_config=user_request.skypilot_config)

.. code-block:: yaml

    admin_policy: examples.admin_policy.enforce_autostop.EnforceAutostopPolicy
