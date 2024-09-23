.. _advanced-policy-config:

Admin Policy Enforcement
========================


SkyPilot provides an **admin policy** mechanism that admins can use to enforce certain policies on users' SkyPilot usage. An admin policy applies
custom validation and mutation logic to a user's tasks and SkyPilot config.

Example usage:

  - Adds custom labels to all tasks [Link to below, fix case]
  - Always Disable Public IP for AWS Tasks [Link to below]
  - Enforce Autostop for all Tasks [Link to below]
 

To implement and use an admin policy:

    - Admins writes a simple Python package with a policy class that implements SkyPilot's ``sky.AdminPolicy`` interface; 
    - Admins distributes this package to users;
    - Users simply set the ``admin_policy`` field in the SkyPilot config file ``~/.sky/config.yaml`` for the policy to go into effect.


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
    You can test the existence of the policy by running:

    .. code-block:: bash

        python -c "from mypackage.subpackage import MyPolicy"


Admin-Side
~~~~~~~~~~

An admin can distribute the Python package to users with a pre-defined policy. The
policy should follow the following interface:

.. code-block:: python

    import sky

    class MyPolicy(sky.AdminPolicy):
        @classmethod
        def validate_and_mutate(cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
            # Logic for validate and modify user requests.
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
        """Example policy: rejects all user requests."""

        @classmethod
        def validate_and_mutate(cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
            """Rejects all user requests."""
            raise RuntimeError("This policy rejects all user requests.")

.. code-block:: yaml

    admin_policy: examples.admin_policy.reject_all.RejectAllPolicy


Add Kubernetes Labels for all Tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    class AddLabelsPolicy(sky.AdminPolicy):
        """Example policy: adds a kubernetes label for skypilot_config."""

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
        """Example policy: disables public IP for all tasks."""

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
        """Example policy: enforce autostop for all tasks."""

        @classmethod
        def validate_and_mutate(
                cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
            """Enforces autostop for all tasks."""
            request_options = user_request.request_options
            # Request options is None when a task is executed with `jobs launch` or
            # `sky serve up`.
            if request_options is None:
                return sky.MutatedUserRequest(
                    task=user_request.task,
                    skypilot_config=user_request.skypilot_config)
            idle_minutes_to_autostop = request_options.idle_minutes_to_autostop
            # Enforce autostop/down to be set for all tasks for new clusters.
            if not request_options.cluster_running and (
                    idle_minutes_to_autostop is None or
                    idle_minutes_to_autostop < 0):
                raise RuntimeError('Autostop/down must be set for all newly '
                                'launched clusters.')
            return sky.MutatedUserRequest(
                task=user_request.task,
                skypilot_config=user_request.skypilot_config)


.. code-block:: yaml

    admin_policy: examples.admin_policy.enforce_autostop.EnforceAutostopPolicy
