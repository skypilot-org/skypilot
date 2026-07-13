.. _cloud-permissions-openstack:

OpenStack
=========

SkyPilot uses the OpenStack SDK with a named profile from
``~/.config/openstack/clouds.yaml``. For long-lived credentials, application
credentials are preferred over a user password when the deployment supports
them. Secrets can be kept separately in ``secure.yaml``.

The selected project must allow SkyPilot to:

* list Nova flavors and availability zones;
* find Glance images;
* find Neutron networks and ports;
* create, start, stop, and delete Nova servers; and
* create and delete security groups, security group rules, and floating IPs.

The Neutron deployment must expose the ``standard-attr-tag`` resource tags
extension for security groups and floating IPs, and the project must be allowed
to set those tags. SkyPilot combines tags with cluster-specific descriptions
and Nova metadata to decide which resources it owns.

SkyPilot uses an existing tenant network. It does not create or delete tenant
networks, subnets, or routers. When ``security_group_name`` is configured,
SkyPilot treats that security group as user-managed and does not modify or
delete it. The group must already allow SSH on port 22 from the operator and
any traffic required by the workload.

The first OpenStack integration supports direct image boot only. It injects the
SkyPilot SSH key through cloud-init user data instead of creating a Nova key
pair. The selected flavor's root disk must be at least as large as the task's
``disk_size``. The Glance image must be a cloud-init-enabled Debian or Ubuntu
image. Cinder boot-from-volume is not supported yet.

See the `OpenStack SDK configuration documentation
<https://docs.openstack.org/openstacksdk/latest/user/config/configuration.html>`_
for profile and certificate options.
