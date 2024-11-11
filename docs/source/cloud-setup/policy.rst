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
policy should implement the ``sky.AdminPolicy`` `interface <https://github.com/skypilot-org/skypilot/blob/master/sky/admin_policy.py>`_:


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
the :ref:`task <yaml-spec>` and the :ref:`global skypilot config <config-yaml>`,
giving admins a lot of flexibility to control user's SkyPilot usage.

An ``AdminPolicy`` can be used to both validate and mutate user requests. If
a request should be rejected, the policy should raise an exception.


The ``sky.Config`` and ``sky.RequestOptions`` classes are defined as follows:

.. literalinclude:: ../../../sky/skypilot_config.py
    :language: python
    :pyobject: Config
    :caption: `Config Class <https://github.com/skypilot-org/skypilot/blob/master/sky/skypilot_config.py>`_


.. literalinclude:: ../../../sky/admin_policy.py
    :language: python
    :pyobject: RequestOptions
    :caption: `RequestOptions Class <https://github.com/skypilot-org/skypilot/blob/master/sky/admin_policy.py>`_

.. note::

    The ``sky.AdminPolicy`` should be idempotent. In other words, it should be safe to apply the policy multiple times to the same user request.

Example Policies    
----------------

We have provided a few example policies in `examples/admin_policy/example_policy <https://github.com/skypilot-org/skypilot/tree/master/examples/admin_policy/example_policy>`_. You can test these policies by installing the example policy package in your Python environment.

.. code-block:: bash

    git clone https://github.com/skypilot-org/skypilot.git
    cd skypilot
    pip install examples/admin_policy/example_policy

Reject All
~~~~~~~~~~

.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/skypilot_policy.py
    :language: python
    :pyobject: RejectAllPolicy
    :caption: `RejectAllPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/skypilot_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/reject_all.yaml
    :language: yaml
    :caption: `Config YAML for using RejectAllPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/reject_all.yaml>`_

.. _kubernetes-labels-policy:

Add Labels for all Tasks on Kubernetes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/skypilot_policy.py
    :language: python
    :pyobject: AddLabelsPolicy
    :caption: `AddLabelsPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/skypilot_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/add_labels.yaml
    :language: yaml
    :caption: `Config YAML for using AddLabelsPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/add_labels.yaml>`_


.. _disable-public-ip-policy:
    
Always Disable Public IP for AWS Tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/skypilot_policy.py
    :language: python
    :pyobject: DisablePublicIpPolicy
    :caption: `DisablePublicIpPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/skypilot_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/disable_public_ip.yaml
    :language: yaml
    :caption: `Config YAML for using DisablePublicIpPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/disable_public_ip.yaml>`_

.. _use-spot-for-gpu-policy:

Use Spot for all GPU Tasks
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

Enforce Autostop for all Tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/skypilot_policy.py
    :language: python
    :pyobject: EnforceAutostopPolicy
    :caption: `EnforceAutostopPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/skypilot_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/enforce_autostop.yaml
    :language: yaml
    :caption: `Config YAML for using EnforceAutostopPolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/enforce_autostop.yaml>`_


.. _dynamic-kubernetes-contexts-update-policy:

Dynamically Update Kubernetes Contexts to Use
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. literalinclude:: ../../../examples/admin_policy/example_policy/example_policy/skypilot_policy.py
    :language: python
    :pyobject: DynamicKubernetesContextsUpdatePolicy
    :caption: `DynamicKubernetesContextsUpdatePolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/example_policy/example_policy/skypilot_policy.py>`_

.. literalinclude:: ../../../examples/admin_policy/dynamic_kubernetes_contexts_update.yaml
    :language: yaml
    :caption: `Config YAML for using DynamicKubernetesContextsUpdatePolicy <https://github.com/skypilot-org/skypilot/blob/master/examples/admin_policy/dynamic_kubernetes_contexts_update.yaml>`_
