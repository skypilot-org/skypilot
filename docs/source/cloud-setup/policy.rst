.. _advanced-policy-config:

Admin Policy Enforcement
========================


SkyPilot provides an **admin policy** mechanism that admins can use to enforce certain policies on users' SkyPilot usage. An admin policy applies
custom validation and mutation logic to a user's tasks and SkyPilot config.

Example usage:

- :ref:`kubernetes-labels-policy`
- :ref:`disable-public-ip-policy`
- :ref:`use-spot-for-gpu-policy`
- :ref:`enforce-autostop-policy`
- :ref:`dynamic-kubernetes-contexts-update-policy`
- :ref:`use-local-gcp-credentials-policy`

Overview
--------

Admin policies are :ref:`implemented in Python packages <implement-admin-policy>` first, then get distributed and applied at the :ref:`server-side <server-side-admin-policy>` and/or the :ref:`client-side <client-side-admin-policy>`. If both are set, the policy at client-side will be applied first, the mutated tasks and SkyPilot config will then be processed by the policy at server-side.

.. _client-side-admin-policy:

Client-side
~~~~~~~~~~~

If you want the policy get applied only on your local machine or you don't have a :ref:`centralized API server <sky-api-server>` deployed, you can apply the policy at the client-side.

First, install the Python package that implements the policy:

.. code-block:: bash

    pip install mypackage.subpackage

Then, set the :ref:`admin_policy <config-yaml-admin-policy>` field in :ref:`the SkyPilot config <config-yaml>` to the path of the Python package that implements the policy.

.. code-block:: yaml

    admin_policy: mypackage.subpackage.MyPolicy

Optionally, you can also apply different policies in different projects by leveraging the :ref:`layered config <config-sources-and-overrides>`, e.g. set a different policy in ``$pwd/.sky.yaml`` for the current project:

.. code-block:: yaml

    admin_policy: mypackage.subpackage.AnotherPolicy

.. hint::

    SkyPilot loads the policy from the given package in the same Python environment.
    You can test the existence of the policy by running:

    .. code-block:: bash

        python -c "from mypackage.subpackage import MyPolicy"

.. _server-side-admin-policy:

Server-side
~~~~~~~~~~~

If you have a :ref:`centralized API server <sky-api-server>` deployed, you can enforce a policy for all users by setting it at the server-side.

First, install the Python package that implements the policy on the API server host:

.. code-block:: bash

    pip install mypackage.subpackage

For helm deployment, refer to :ref:`sky-api-server-admin-policy` to install the policy package.

Then, open the server's dashboard, go to :ref:`the server's SkyPilot config <sky-api-server-config>` and set the :ref:`admin_policy <config-yaml-admin-policy>` field to the path of the Python package that implements the policy.

.. code-block:: yaml

    admin_policy: mypackage.subpackage.MyPolicy

.. _implement-admin-policy:

Implement an admin policy
~~~~~~~~~~~~~~~~~~~~~~~~~

Admin policies are implemented by extending the ``sky.AdminPolicy`` `interface <https://github.com/skypilot-org/skypilot/blob/master/sky/admin_policy.py>`_:


.. literalinclude:: ../../../sky/admin_policy.py
    :language: python
    :pyobject: AdminPolicy
    :caption: `AdminPolicy Interface <https://github.com/skypilot-org/skypilot/blob/master/sky/admin_policy.py>`_


Your custom admin policy should look like this:

.. code-block:: python

    import sky

    class MyPolicy(sky.AdminPolicy):
        @classmethod
        def validate_and_mutate(cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
            # Logic for validate and modify user requests.
            ...
            return sky.MutatedUserRequest(user_request.task,
                                          user_request.skypilot_config)


``UserRequest`` and ``MutatedUserRequest`` are defined as follows (see `source code <https://github.com/skypilot-org/skypilot/blob/master/sky/admin_policy.py>`_ for more details):


.. literalinclude:: ../../../sky/admin_policy.py
    :language: python
    :pyobject: UserRequest
    :caption: `UserRequest Class <https://github.com/skypilot-org/skypilot/blob/master/sky/admin_policy.py>`_

.. literalinclude:: ../../../sky/admin_policy.py
    :language: python
    :pyobject: MutatedUserRequest
    :caption: `MutatedUserRequest Class <https://github.com/skypilot-org/skypilot/blob/master/sky/admin_policy.py>`_


In other words, an ``AdminPolicy`` can mutate any fields of a user request, including
the :ref:`task <yaml-spec>` and the :ref:`skypilot config <config-yaml>` for that specific user request,
giving admins a lot of flexibility to control user's SkyPilot usage.

An ``AdminPolicy`` can be used to both validate and mutate user requests. If
a request should be rejected, the policy should raise an exception.


The ``sky.Config`` and ``sky.RequestOptions`` classes are defined as follows:

.. literalinclude:: ../../../sky/utils/config_utils.py
    :language: python
    :pyobject: Config
    :caption: `Config Class <https://github.com/skypilot-org/skypilot/blob/master/sky/utils/config_utils.py>`_


.. literalinclude:: ../../../sky/admin_policy.py
    :language: python
    :pyobject: RequestOptions
    :caption: `RequestOptions Class <https://github.com/skypilot-org/skypilot/blob/master/sky/admin_policy.py>`_


Example policies
----------------

We have provided a few example policies in `examples/admin_policy/example_policy <https://github.com/skypilot-org/skypilot/tree/master/examples/admin_policy/example_policy>`_. You can test these policies by installing the example policy package in your Python environment.

.. code-block:: bash

    git clone https://github.com/skypilot-org/skypilot.git
    cd skypilot
    pip install examples/admin_policy/example_policy

Reject all tasks
~~~~~~~~~~~~~~~~

.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/skypilot_policy.py
    :language: python
    :pyobject: RejectAllPolicy
    :caption: `RejectAllPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/skypilot_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/reject_all.yaml
    :language: yaml
    :caption: `Config YAML for using RejectAllPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/reject_all.yaml>`_

.. _kubernetes-labels-policy:

Add labels for all tasks on Kubernetes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/skypilot_policy.py
    :language: python
    :pyobject: AddLabelsPolicy
    :caption: `AddLabelsPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/skypilot_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/add_labels.yaml
    :language: yaml
    :caption: `Config YAML for using AddLabelsPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/add_labels.yaml>`_


.. _disable-public-ip-policy:

Always disable public IP for AWS tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/skypilot_policy.py
    :language: python
    :pyobject: DisablePublicIpPolicy
    :caption: `DisablePublicIpPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/skypilot_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/disable_public_ip.yaml
    :language: yaml
    :caption: `Config YAML for using DisablePublicIpPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/disable_public_ip.yaml>`_

.. _use-spot-for-gpu-policy:

Use spot for all GPU tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~

..
.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/skypilot_policy.py
    :language: python
    :pyobject: UseSpotForGpuPolicy
    :caption: `UseSpotForGpuPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/skypilot_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/use_spot_for_gpu.yaml
    :language: yaml
    :caption: `Config YAML for using UseSpotForGpuPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/use_spot_for_gpu.yaml>`_

.. _enforce-autostop-policy:

Enforce autostop for all tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/skypilot_policy.py
    :language: python
    :pyobject: EnforceAutostopPolicy
    :caption: `EnforceAutostopPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/skypilot_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/enforce_autostop.yaml
    :language: yaml
    :caption: `Config YAML for using EnforceAutostopPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/enforce_autostop.yaml>`_


.. _dynamic-kubernetes-contexts-update-policy:

Dynamically update Kubernetes contexts to use
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/skypilot_policy.py
    :language: python
    :pyobject: DynamicKubernetesContextsUpdatePolicy
    :caption: `DynamicKubernetesContextsUpdatePolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/skypilot_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/dynamic_kubernetes_contexts_update.yaml
    :language: yaml
    :caption: `Config YAML for using DynamicKubernetesContextsUpdatePolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/dynamic_kubernetes_contexts_update.yaml>`_

.. _use-local-gcp-credentials-policy:

Use local GCP credentials for all tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/client_policy.py
    :language: python
    :pyobject: UseLocalGcpCredentialsPolicy
    :caption: `UseLocalGcpCredentialsPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/client_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/use_local_gcp_credentials.yaml
    :language: yaml
    :caption: `Config YAML for using UseLocalGcpCredentialsPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/use_local_gcp_credentials.yaml>`_

.. note::

    This policy only take effects when applied at :ref:`client-side <client-side-admin-policy>`. Use this policy at the :ref:`server-side <server-side-admin-policy>` will be a no-op.
