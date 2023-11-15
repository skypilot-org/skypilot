import copy
import json
import logging
import os
import time
from functools import partial
import typing
from typing import Dict, List, Set, Tuple

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient import discovery, errors

if typing.TYPE_CHECKING:
    import google

from sky.skylet.providers.gcp.node import (
    MAX_POLLS,
    POLL_INTERVAL,
    GCPNodeType,
    GCPCompute,
)
from sky.skylet.providers.gcp.constants import (
    SKYPILOT_VPC_NAME,
    VPC_TEMPLATE,
    FIREWALL_RULES_TEMPLATE,
    FIREWALL_RULES_REQUIRED,
    VM_MINIMAL_PERMISSIONS,
    TPU_MINIMAL_PERMISSIONS,
)
from sky.utils import common_utils
from ray.autoscaler._private.util import check_legacy_fields

logger = logging.getLogger(__name__)

VERSION = "v1"
TPU_VERSION = "v2alpha"  # change once v2 is stable

RAY = "ray-autoscaler"
DEFAULT_SERVICE_ACCOUNT_ID = RAY + "-sa-" + VERSION
SERVICE_ACCOUNT_EMAIL_TEMPLATE = "{account_id}@{project_id}.iam.gserviceaccount.com"
DEFAULT_SERVICE_ACCOUNT_CONFIG = {
    "displayName": "Ray Autoscaler Service Account ({})".format(VERSION),
}

SKYPILOT = "skypilot"
SKYPILOT_SERVICE_ACCOUNT_ID = SKYPILOT + "-" + VERSION
SKYPILOT_SERVICE_ACCOUNT_EMAIL_TEMPLATE = (
    "{account_id}@{project_id}.iam.gserviceaccount.com"
)
SKYPILOT_SERVICE_ACCOUNT_CONFIG = {
    "displayName": "SkyPilot Service Account ({})".format(VERSION),
}

# Those roles will be always added.
# NOTE: `serviceAccountUser` allows the head node to create workers with
# a serviceAccount. `roleViewer` allows the head node to run bootstrap_gcp.
DEFAULT_SERVICE_ACCOUNT_ROLES = [
    "roles/storage.objectAdmin",
    "roles/compute.admin",
    "roles/iam.serviceAccountUser",
    "roles/iam.roleViewer",
]
# Those roles will only be added if there are TPU nodes defined in config.
TPU_SERVICE_ACCOUNT_ROLES = ["roles/tpu.admin"]

# If there are TPU nodes in config, this field will be set
# to True in config["provider"].
HAS_TPU_PROVIDER_FIELD = "_has_tpus"

# NOTE: iam.serviceAccountUser allows the Head Node to create worker nodes
# with ServiceAccounts.


def _skypilot_log_error_and_exit_for_failover(error: str) -> None:
    """Logs an message then raises a specific RuntimeError to trigger failover.

    Mainly used for handling VPC/subnet errors before nodes are launched.
    """
    # NOTE: keep. The backend looks for this to know no nodes are launched.
    prefix = "SKYPILOT_ERROR_NO_NODES_LAUNCHED: "
    raise RuntimeError(prefix + error)


def get_node_type(node: dict) -> GCPNodeType:
    """Returns node type based on the keys in ``node``.

    This is a very simple check. If we have a ``machineType`` key,
    this is a Compute instance. If we don't have a ``machineType`` key,
    but we have ``acceleratorType``, this is a TPU. Otherwise, it's
    invalid and an exception is raised.

    This works for both node configs and API returned nodes.
    """

    if "machineType" not in node and "acceleratorType" not in node:
        raise ValueError(
            "Invalid node. For a Compute instance, 'machineType' is "
            "required. "
            "For a TPU instance, 'acceleratorType' and no 'machineType' "
            "is required. "
            f"Got {list(node)}"
        )

    if "machineType" not in node and "acceleratorType" in node:
        return GCPNodeType.TPU
    return GCPNodeType.COMPUTE


def wait_for_crm_operation(operation, crm):
    """Poll for cloud resource manager operation until finished."""
    logger.info(
        "wait_for_crm_operation: "
        "Waiting for operation {} to finish...".format(operation)
    )

    for _ in range(MAX_POLLS):
        result = crm.operations().get(name=operation["name"]).execute()
        if "error" in result:
            raise Exception(result["error"])

        if "done" in result and result["done"]:
            logger.info("wait_for_crm_operation: Operation done.")
            break

        time.sleep(POLL_INTERVAL)

    return result


def wait_for_compute_global_operation(project_name, operation, compute):
    """Poll for global compute operation until finished."""
    logger.info(
        "wait_for_compute_global_operation: "
        "Waiting for operation {} to finish...".format(operation["name"])
    )

    for _ in range(MAX_POLLS):
        result = (
            compute.globalOperations()
            .get(
                project=project_name,
                operation=operation["name"],
            )
            .execute()
        )
        if "error" in result:
            raise Exception(result["error"])

        if result["status"] == "DONE":
            logger.info("wait_for_compute_global_operation: Operation done.")
            break

        time.sleep(POLL_INTERVAL)

    return result


def key_pair_name(i, region, project_id, ssh_user):
    """Returns the ith default gcp_key_pair_name."""
    key_name = "{}_gcp_{}_{}_{}_{}".format(SKYPILOT, region, project_id, ssh_user, i)
    return key_name


def key_pair_paths(key_name):
    """Returns public and private key paths for a given key_name."""
    public_key_path = os.path.expanduser("~/.ssh/{}.pub".format(key_name))
    private_key_path = os.path.expanduser("~/.ssh/{}.pem".format(key_name))
    return public_key_path, private_key_path


def generate_rsa_key_pair():
    """Create public and private ssh-keys."""

    key = rsa.generate_private_key(
        backend=default_backend(), public_exponent=65537, key_size=2048
    )

    public_key = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        .decode("utf-8")
    )

    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    return public_key, pem


def _has_tpus_in_node_configs(config: dict) -> bool:
    """Check if any nodes in config are TPUs."""
    node_configs = [
        node_type["node_config"]
        for node_type in config["available_node_types"].values()
    ]
    return any(get_node_type(node) == GCPNodeType.TPU for node in node_configs)


def _is_head_node_a_tpu(config: dict) -> bool:
    """Check if the head node is a TPU."""
    node_configs = {
        node_id: node_type["node_config"]
        for node_id, node_type in config["available_node_types"].items()
    }
    return get_node_type(node_configs[config["head_node_type"]]) == GCPNodeType.TPU


def _create_crm(gcp_credentials=None):
    return discovery.build(
        "cloudresourcemanager", "v1", credentials=gcp_credentials, cache_discovery=False
    )


def _create_iam(gcp_credentials=None):
    return discovery.build(
        "iam", "v1", credentials=gcp_credentials, cache_discovery=False
    )


def _create_compute(gcp_credentials=None):
    return discovery.build(
        "compute", "v1", credentials=gcp_credentials, cache_discovery=False
    )


def _create_tpu(gcp_credentials=None):
    return discovery.build(
        "tpu",
        TPU_VERSION,
        credentials=gcp_credentials,
        cache_discovery=False,
        discoveryServiceUrl="https://tpu.googleapis.com/$discovery/rest",
    )


def construct_clients_from_provider_config(provider_config):
    """
    Attempt to fetch and parse the JSON GCP credentials from the provider
    config yaml file.

    tpu resource (the last element of the tuple) will be None if
    `_has_tpus` in provider config is not set or False.
    """
    gcp_credentials = provider_config.get("gcp_credentials")
    if gcp_credentials is None:
        logger.debug(
            "gcp_credentials not found in cluster yaml file. "
            "Falling back to GOOGLE_APPLICATION_CREDENTIALS "
            "environment variable."
        )
        tpu_resource = (
            _create_tpu()
            if provider_config.get(HAS_TPU_PROVIDER_FIELD, False)
            else None
        )
        # If gcp_credentials is None, then discovery.build will search for
        # credentials in the local environment.
        return _create_crm(), _create_iam(), _create_compute(), tpu_resource

    assert (
        "type" in gcp_credentials
    ), "gcp_credentials cluster yaml field missing 'type' field."
    assert (
        "credentials" in gcp_credentials
    ), "gcp_credentials cluster yaml field missing 'credentials' field."

    cred_type = gcp_credentials["type"]
    credentials_field = gcp_credentials["credentials"]

    if cred_type == "service_account":
        # If parsing the gcp_credentials failed, then the user likely made a
        # mistake in copying the credentials into the config yaml.
        try:
            service_account_info = json.loads(credentials_field)
        except json.decoder.JSONDecodeError:
            raise RuntimeError(
                "gcp_credentials found in cluster yaml file but "
                "formatted improperly."
            )
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info
        )
    elif cred_type == "credentials_token":
        # Otherwise the credentials type must be credentials_token.
        credentials = OAuthCredentials(credentials_field)

    tpu_resource = (
        _create_tpu(credentials)
        if provider_config.get(HAS_TPU_PROVIDER_FIELD, False)
        else None
    )

    return (
        _create_crm(credentials),
        _create_iam(credentials),
        _create_compute(credentials),
        tpu_resource,
    )


def bootstrap_gcp(config):
    config = copy.deepcopy(config)
    check_legacy_fields(config)
    # Used internally to store head IAM role.
    config["head_node"] = {}

    # Check if we have any TPUs defined, and if so,
    # insert that information into the provider config
    if _has_tpus_in_node_configs(config):
        config["provider"][HAS_TPU_PROVIDER_FIELD] = True

    crm, iam, compute, tpu = construct_clients_from_provider_config(config["provider"])

    config = _configure_project(config, crm)
    config = _configure_iam_role(config, crm, iam)
    config = _configure_key_pair(config, compute)
    config = _configure_subnet(config, compute)

    return config


def _configure_project(config, crm):
    """Setup a Google Cloud Platform Project.

    Google Compute Platform organizes all the resources, such as storage
    buckets, users, and instances under projects. This is different from
    aws ec2 where everything is global.
    """
    config = copy.deepcopy(config)

    project_id = config["provider"].get("project_id")
    assert config["provider"]["project_id"] is not None, (
        "'project_id' must be set in the 'provider' section of the autoscaler"
        " config. Notice that the project id must be globally unique."
    )
    project = _get_project(project_id, crm)

    if project is None:
        #  Project not found, try creating it
        _create_project(project_id, crm)
        project = _get_project(project_id, crm)

    assert project is not None, "Failed to create project"
    assert (
        project["lifecycleState"] == "ACTIVE"
    ), "Project status needs to be ACTIVE, got {}".format(project["lifecycleState"])

    config["provider"]["project_id"] = project["projectId"]

    return config


def _is_permission_satisfied(
    service_account, crm, iam, required_permissions, required_roles
):
    """Check if either of the roles or permissions are satisfied."""
    if service_account is None:
        return False, None

    project_id = service_account["projectId"]
    email = service_account["email"]

    member_id = "serviceAccount:" + email

    required_permissions = set(required_permissions)
    policy = crm.projects().getIamPolicy(resource=project_id, body={}).execute()
    original_policy = copy.deepcopy(policy)
    already_configured = True

    logger.info(f"_configure_iam_role: Checking permissions for {email}...")

    # Check the roles first, as checking the permission requires more API calls and
    # permissions.
    for role in required_roles:
        role_exists = False
        for binding in policy["bindings"]:
            if binding["role"] == role:
                if member_id not in binding["members"]:
                    logger.info(
                        f"_configure_iam_role: role {role} is not attached to {member_id}..."
                    )
                    binding["members"].append(member_id)
                    already_configured = False
                role_exists = True

        if not role_exists:
            logger.info(f"_configure_iam_role: role {role} does not exist.")
            already_configured = False
            policy["bindings"].append(
                {
                    "members": [member_id],
                    "role": role,
                }
            )

    if already_configured:
        # In some managed environments, an admin needs to grant the
        # roles, so only call setIamPolicy if needed.
        return True, policy

    for binding in original_policy["bindings"]:
        if member_id in binding["members"]:
            role = binding["role"]
            try:
                role_definition = iam.projects().roles().get(name=role).execute()
            except TypeError as e:
                if "does not match the pattern" in str(e):
                    logger.info(
                        f"_configure_iam_role: fail to check permission for built-in role {role}. skipped."
                    )
                    permissions = []
                else:
                    raise
            else:
                permissions = role_definition["includedPermissions"]
            required_permissions -= set(permissions)
        if not required_permissions:
            break
    if not required_permissions:
        # All required permissions are already granted.
        return True, policy
    logger.info(f"_configure_iam_role: missing permisisons {required_permissions}")

    return False, policy


def _configure_iam_role(config, crm, iam):
    """Setup a gcp service account with IAM roles.

    Creates a gcp service acconut and binds IAM roles which allow it to control
    control storage/compute services. Specifically, the head node needs to have
    an IAM role that allows it to create further gce instances and store items
    in google cloud storage.

    TODO: Allow the name/id of the service account to be configured
    """
    config = copy.deepcopy(config)

    email = SKYPILOT_SERVICE_ACCOUNT_EMAIL_TEMPLATE.format(
        account_id=SKYPILOT_SERVICE_ACCOUNT_ID,
        project_id=config["provider"]["project_id"],
    )
    service_account = _get_service_account(email, config, iam)

    permissions = VM_MINIMAL_PERMISSIONS
    roles = DEFAULT_SERVICE_ACCOUNT_ROLES
    if config["provider"].get(HAS_TPU_PROVIDER_FIELD, False):
        roles = DEFAULT_SERVICE_ACCOUNT_ROLES + TPU_SERVICE_ACCOUNT_ROLES
        permissions = VM_MINIMAL_PERMISSIONS + TPU_MINIMAL_PERMISSIONS

    satisfied, policy = _is_permission_satisfied(
        service_account, crm, iam, permissions, roles
    )

    if not satisfied:
        # SkyPilot: Fallback to the old ray service account name for
        # backwards compatibility. Users using GCP before #2112 have
        # the old service account setup setup in their GCP project,
        # and the user may not have the permissions to create the
        # new service account. This is to ensure that the old service
        # account is still usable.
        email = SERVICE_ACCOUNT_EMAIL_TEMPLATE.format(
            account_id=DEFAULT_SERVICE_ACCOUNT_ID,
            project_id=config["provider"]["project_id"],
        )
        logger.info(f"_configure_iam_role: Fallback to service account {email}")

        ray_service_account = _get_service_account(email, config, iam)
        ray_satisfied, _ = _is_permission_satisfied(
            ray_service_account, crm, iam, permissions, roles
        )
        logger.info(
            "_configure_iam_role: "
            f"Fallback to service account {email} succeeded? {ray_satisfied}"
        )

        if ray_satisfied:
            service_account = ray_service_account
            satisfied = ray_satisfied
        elif service_account is None:
            logger.info(
                "_configure_iam_role: "
                "Creating new service account {}".format(SKYPILOT_SERVICE_ACCOUNT_ID)
            )
            # SkyPilot: a GCP user without the permission to create a service
            # account will fail here.
            service_account = _create_service_account(
                SKYPILOT_SERVICE_ACCOUNT_ID,
                SKYPILOT_SERVICE_ACCOUNT_CONFIG,
                config,
                iam,
            )
            satisfied, policy = _is_permission_satisfied(
                service_account, crm, iam, permissions, roles
            )

    assert service_account is not None, "Failed to create service account"

    if not satisfied:
        logger.info(
            "_configure_iam_role: " f"Adding roles to service account {email}..."
        )
        _add_iam_policy_binding(service_account, policy, crm, iam)

    account_dict = {
        "email": service_account["email"],
        # NOTE: The amount of access is determined by the scope + IAM
        # role of the service account. Even if the cloud-platform scope
        # gives (scope) access to the whole cloud-platform, the service
        # account is limited by the IAM rights specified below.
        "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
    }
    if _is_head_node_a_tpu(config):
        # SKY: The API for TPU VM is slightly different from normal compute instances.
        # See https://cloud.google.com/tpu/docs/reference/rest/v2alpha1/projects.locations.nodes#Node
        account_dict["scope"] = account_dict["scopes"]
        account_dict.pop("scopes")
        config["head_node"]["serviceAccount"] = account_dict
    else:
        config["head_node"]["serviceAccounts"] = [account_dict]

    return config


def _configure_key_pair(config, compute):
    """Configure SSH access, using an existing key pair if possible.

    Creates a project-wide ssh key that can be used to access all the instances
    unless explicitly prohibited by instance config.

    The ssh-keys created by ray are of format:

      [USERNAME]:ssh-rsa [KEY_VALUE] [USERNAME]

    where:

      [USERNAME] is the user for the SSH key, specified in the config.
      [KEY_VALUE] is the public SSH key value.
    """
    config = copy.deepcopy(config)

    if "ssh_private_key" in config["auth"]:
        return config

    ssh_user = config["auth"]["ssh_user"]

    project = compute.projects().get(project=config["provider"]["project_id"]).execute()

    # Key pairs associated with project meta data. The key pairs are general,
    # and not just ssh keys.
    ssh_keys_str = next(
        (
            item
            for item in project["commonInstanceMetadata"].get("items", [])
            if item["key"] == "ssh-keys"
        ),
        {},
    ).get("value", "")

    ssh_keys = ssh_keys_str.split("\n") if ssh_keys_str else []

    # Try a few times to get or create a good key pair.
    key_found = False
    for i in range(10):
        key_name = key_pair_name(
            i, config["provider"]["region"], config["provider"]["project_id"], ssh_user
        )
        public_key_path, private_key_path = key_pair_paths(key_name)

        for ssh_key in ssh_keys:
            key_parts = ssh_key.split(" ")
            if len(key_parts) != 3:
                continue

            if key_parts[2] == ssh_user and os.path.exists(private_key_path):
                # Found a key
                key_found = True
                break

        # Writing the new ssh key to the filesystem fails if the ~/.ssh
        # directory doesn't already exist.
        os.makedirs(os.path.expanduser("~/.ssh"), exist_ok=True)

        # Create a key since it doesn't exist locally or in GCP
        if not key_found and not os.path.exists(private_key_path):
            logger.info(
                "_configure_key_pair: Creating new key pair {}".format(key_name)
            )
            public_key, private_key = generate_rsa_key_pair()

            _create_project_ssh_key_pair(project, public_key, ssh_user, compute)

            # Create the directory if it doesn't exists
            private_key_dir = os.path.dirname(private_key_path)
            os.makedirs(private_key_dir, exist_ok=True)

            # We need to make sure to _create_ the file with the right
            # permissions. In order to do that we need to change the default
            # os.open behavior to include the mode we want.
            with open(
                private_key_path,
                "w",
                opener=partial(os.open, mode=0o600),
            ) as f:
                f.write(private_key)

            with open(public_key_path, "w") as f:
                f.write(public_key)

            key_found = True

            break

        if key_found:
            break

    assert key_found, "SSH keypair for user {} not found for {}".format(
        ssh_user, private_key_path
    )
    assert os.path.exists(
        private_key_path
    ), "Private key file {} not found for user {}".format(private_key_path, ssh_user)

    logger.info(
        "_configure_key_pair: "
        "Private key not specified in config, using"
        "{}".format(private_key_path)
    )

    config["auth"]["ssh_private_key"] = private_key_path

    return config


def _check_firewall_rules(vpc_name, config, compute):
    """Check if the firewall rules in the VPC are sufficient."""
    required_rules = FIREWALL_RULES_REQUIRED.copy()

    operation = compute.networks().getEffectiveFirewalls(
        project=config["provider"]["project_id"], network=vpc_name
    )
    response = operation.execute()
    if len(response) == 0:
        return False
    effective_rules = response["firewalls"]

    def _merge_and_refine_rule(rules):
        """Returns the reformatted rules from the firewall rules

        The function translates firewall rules fetched from the cloud provider
        to a format for simple comparison.

        Example of firewall rules from the cloud:
        [
            {
                ...
                "direction": "INGRESS",
                "allowed": [
                    {"IPProtocol": "tcp", "ports": ['80', '443']},
                    {"IPProtocol": "udp", "ports": ['53']},
                ],
                "sourceRanges": ["10.128.0.0/9"],
            },
            {
                ...
                "direction": "INGRESS",
                "allowed": [{
                    "IPProtocol": "tcp",
                    "ports": ["22"],
                }],
                "sourceRanges": ["0.0.0.0/0"],
            },
        ]

        Returns:
            source2rules: Dict[(direction, sourceRanges) -> Dict(protocol -> Set[ports])]
                Example {
                    ("INGRESS", "10.128.0.0/9"): {"tcp": {80, 443}, "udp": {53}},
                    ("INGRESS", "0.0.0.0/0"): {"tcp": {22}},
                }
        """
        source2rules: Dict[Tuple[str, str], Dict[str, Set[int]]] = {}
        source2allowed_list: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
        for rule in rules:
            # Rules applied to specific VM (targetTags) may not work for the
            # current VM, so should be skipped.
            # Filter by targetTags == ['cluster_name']
            # See https://developers.google.com/resources/api-libraries/documentation/compute/alpha/python/latest/compute_alpha.networks.html#getEffectiveFirewalls # pylint: disable=line-too-long
            tags = rule.get("targetTags", None)
            if tags is not None:
                if len(tags) != 1:
                    continue
                if tags[0] != config["cluster_name"]:
                    continue
            direction = rule.get("direction", "")
            sources = rule.get("sourceRanges", [])
            allowed = rule.get("allowed", [])
            for source in sources:
                key = (direction, source)
                source2allowed_list[key] = source2allowed_list.get(key, []) + allowed
        for direction_source, allowed_list in source2allowed_list.items():
            source2rules[direction_source] = {}
            for allowed in allowed_list:
                # Example of port_list: ["20", "50-60"]
                # If list is empty, it means all ports
                port_list = allowed.get("ports", [])
                port_set = set()
                if port_list == []:
                    port_set.update(set(range(1, 65536)))
                else:
                    for port_range in port_list:
                        parse_ports = port_range.split("-")
                        if len(parse_ports) == 1:
                            port_set.add(int(parse_ports[0]))
                        else:
                            assert (
                                len(parse_ports) == 2
                            ), f"Failed to parse the port range: {port_range}"
                            port_set.update(
                                set(range(int(parse_ports[0]), int(parse_ports[1]) + 1))
                            )
                if allowed["IPProtocol"] not in source2rules[direction_source]:
                    source2rules[direction_source][allowed["IPProtocol"]] = set()
                source2rules[direction_source][allowed["IPProtocol"]].update(port_set)
        return source2rules

    effective_rules = _merge_and_refine_rule(effective_rules)
    required_rules = _merge_and_refine_rule(required_rules)

    for direction_source, allowed_req in required_rules.items():
        if direction_source not in effective_rules:
            return False
        allowed_eff = effective_rules[direction_source]
        # Special case: "all" means allowing all traffic
        if "all" in allowed_eff:
            continue
        # Check if the required ports are a subset of the effective ports
        for protocol, ports_req in allowed_req.items():
            ports_eff = allowed_eff.get(protocol, set())
            if not ports_req.issubset(ports_eff):
                return False
    return True


def _create_rules(config, compute, rules, VPC_NAME, PROJ_ID):
    opertaions = []
    for rule in rules:
        # Query firewall rule by its name (unique in a project).
        # If the rule already exists, delete it first.
        rule_name = rule["name"].format(VPC_NAME=VPC_NAME)
        rule_list = _list_firewall_rules(config, compute, filter=f"(name={rule_name})")
        if len(rule_list) > 0:
            _delete_firewall_rule(config, compute, rule_name)

        body = rule.copy()
        body["name"] = body["name"].format(VPC_NAME=VPC_NAME)
        body["network"] = body["network"].format(PROJ_ID=PROJ_ID, VPC_NAME=VPC_NAME)
        body["selfLink"] = body["selfLink"].format(PROJ_ID=PROJ_ID, VPC_NAME=VPC_NAME)
        op = _create_firewall_rule_submit(config, compute, body)
        opertaions.append(op)
    for op in opertaions:
        wait_for_compute_global_operation(config["provider"]["project_id"], op, compute)


def _network_interface_to_vpc_name(network_interface: Dict[str, str]) -> str:
    """Returns the VPC name of a network interface."""
    return network_interface["network"].split("/")[-1]


def get_usable_vpc_and_subnet(
    config,
) -> Tuple[str, "google.cloud.compute_v1.types.compute.Subnetwork"]:
    """Return a usable VPC and the subnet in it.

    If config['provider']['vpc_name'] is set, return the VPC with the name
    (errors out if not found). When this field is set, no firewall rules
    checking or overrides will take place; it is the user's responsibility to
    properly set up the VPC.

    If not found, create a new one with sufficient firewall rules.

    Returns:
        vpc_name: The name of the VPC network.
        subnet_name: The name of the subnet in the VPC network for the specific
            region.

    Raises:
      RuntimeError: if the user has specified a VPC name but the VPC is not found.
    """
    _, _, compute, _ = construct_clients_from_provider_config(config["provider"])

    # For existing cluster, it is ok to return a VPC and subnet not used by
    # the cluster, as AWS will ignore them.
    # There is a corner case where the multi-node cluster was partially
    # launched, launching the cluster again can cause the nodes located on
    # different VPCs, if VPCs in the project have changed. It should be fine to
    # not handle this special case as we don't want to sacrifice the performance
    # for every launch just for this rare case.

    specific_vpc_to_use = config["provider"].get("vpc_name", None)
    if specific_vpc_to_use is not None:
        vpcnets_all = _list_vpcnets(
            config, compute, filter=f"name={specific_vpc_to_use}"
        )
        # On GCP, VPC names are unique, so it'd be 0 or 1 VPC found.
        assert (
            len(vpcnets_all) <= 1
        ), f"{len(vpcnets_all)} VPCs found with the same name {specific_vpc_to_use}"
        if len(vpcnets_all) == 1:
            # Skip checking any firewall rules if the user has specified a VPC.
            logger.info(f"Using user-specified VPC {specific_vpc_to_use!r}.")
            subnets = _list_subnets(
                config, compute, filter=f'(name="{specific_vpc_to_use}")'
            )
            if not subnets:
                _skypilot_log_error_and_exit_for_failover(
                    f"No subnet for region {config['provider']['region']} found for specified VPC {specific_vpc_to_use!r}. "
                    f"Check the subnets of VPC {specific_vpc_to_use!r} at https://console.cloud.google.com/networking/networks"
                )
            return specific_vpc_to_use, subnets[0]
        else:
            # VPC with this name not found. Error out and let SkyPilot failover.
            _skypilot_log_error_and_exit_for_failover(
                f"No VPC with name {specific_vpc_to_use!r} is found. "
                "To fix: specify a correct VPC name."
            )
            # Should not reach here.

    subnets_all = _list_subnets(config, compute)

    # Check if VPC for subnet has sufficient firewall rules.
    insufficient_vpcs = set()
    for subnet in subnets_all:
        vpc_name = _network_interface_to_vpc_name(subnet)
        if vpc_name in insufficient_vpcs:
            continue
        if _check_firewall_rules(vpc_name, config, compute):
            logger.info(f"get_usable_vpc: Found a usable VPC network {vpc_name!r}.")
            return vpc_name, subnet
        else:
            insufficient_vpcs.add(vpc_name)

    # No usable VPC found. Try to create one.
    proj_id = config["provider"]["project_id"]
    logger.info(f"Creating a default VPC network, {SKYPILOT_VPC_NAME}...")

    # Create a SkyPilot VPC network if it doesn't exist
    vpc_list = _list_vpcnets(config, compute, filter=f"name={SKYPILOT_VPC_NAME}")
    if len(vpc_list) == 0:
        body = VPC_TEMPLATE.copy()
        body["name"] = body["name"].format(VPC_NAME=SKYPILOT_VPC_NAME)
        body["selfLink"] = body["selfLink"].format(
            PROJ_ID=proj_id, VPC_NAME=SKYPILOT_VPC_NAME
        )
        _create_vpcnet(config, compute, body)

    _create_rules(config, compute, FIREWALL_RULES_TEMPLATE, SKYPILOT_VPC_NAME, proj_id)

    usable_vpc_name = SKYPILOT_VPC_NAME
    subnets = _list_subnets(config, compute, filter=f'(name="{usable_vpc_name}")')
    if not subnets:
        _skypilot_log_error_and_exit_for_failover(
            f"No subnet for region {config['provider']['region']} found for generated VPC {usable_vpc_name!r}. "
            "This is probably due to the region being disabled in the account/project_id."
        )
    usable_subnet = subnets[0]
    logger.info(f"A VPC network {SKYPILOT_VPC_NAME} created.")

    return usable_vpc_name, usable_subnet


def _configure_subnet(config, compute):
    """Pick a reasonable subnet if not specified by the config."""
    config = copy.deepcopy(config)

    node_configs = [
        node_type["node_config"]
        for node_type in config["available_node_types"].values()
    ]
    # Rationale: avoid subnet lookup if the network is already
    # completely manually configured

    # networkInterfaces is compute, networkConfig is TPU
    if all(
        "networkInterfaces" in node_config or "networkConfig" in node_config
        for node_config in node_configs
    ):
        return config

    # SkyPilot: make sure there's a usable VPC
    _, default_subnet = get_usable_vpc_and_subnet(config)

    default_interfaces = [
        {
            "subnetwork": default_subnet["selfLink"],
            "accessConfigs": [
                {
                    "name": "External NAT",
                    "type": "ONE_TO_ONE_NAT",
                }
            ],
        }
    ]

    for node_config in node_configs:
        # The not applicable key will be removed during node creation

        # compute
        if "networkInterfaces" not in node_config:
            node_config["networkInterfaces"] = copy.deepcopy(default_interfaces)
        # TPU
        if "networkConfig" not in node_config:
            node_config["networkConfig"] = copy.deepcopy(default_interfaces)[0]
            node_config["networkConfig"].pop("accessConfigs")

    return config


def _create_firewall_rule_submit(config, compute, body):
    operation = (
        compute.firewalls()
        .insert(project=config["provider"]["project_id"], body=body)
        .execute()
    )
    return operation


def _delete_firewall_rule(config, compute, name):
    operation = (
        compute.firewalls()
        .delete(project=config["provider"]["project_id"], firewall=name)
        .execute()
    )
    response = wait_for_compute_global_operation(
        config["provider"]["project_id"], operation, compute
    )
    return response


def _list_firewall_rules(config, compute, filter=None):
    response = (
        compute.firewalls()
        .list(
            project=config["provider"]["project_id"],
            filter=filter,
        )
        .execute()
    )
    return response["items"] if "items" in response else []


def _create_vpcnet(config, compute, body):
    operation = (
        compute.networks()
        .insert(project=config["provider"]["project_id"], body=body)
        .execute()
    )
    response = wait_for_compute_global_operation(
        config["provider"]["project_id"], operation, compute
    )
    return response


def _list_vpcnets(config, compute, filter=None):
    response = (
        compute.networks()
        .list(
            project=config["provider"]["project_id"],
            filter=filter,
        )
        .execute()
    )

    return (
        list(sorted(response["items"], key=lambda x: x["name"]))
        if "items" in response
        else []
    )


def _list_subnets(
    config, compute, filter=None
) -> List["google.cloud.compute_v1.types.compute.Subnetwork"]:
    response = (
        compute.subnetworks()
        .list(
            project=config["provider"]["project_id"],
            region=config["provider"]["region"],
            filter=filter,
        )
        .execute()
    )

    return response["items"] if "items" in response else []


def _get_subnet(config, subnet_id, compute):
    subnet = (
        compute.subnetworks()
        .get(
            project=config["provider"]["project_id"],
            region=config["provider"]["region"],
            subnetwork=subnet_id,
        )
        .execute()
    )

    return subnet


def _get_project(project_id, crm):
    try:
        project = crm.projects().get(projectId=project_id).execute()
    except errors.HttpError as e:
        if e.resp.status != 403:
            raise
        project = None

    return project


def _create_project(project_id, crm):
    operation = (
        crm.projects()
        .create(body={"projectId": project_id, "name": project_id})
        .execute()
    )

    result = wait_for_crm_operation(operation, crm)

    return result


def _get_service_account(account, config, iam):
    project_id = config["provider"]["project_id"]
    full_name = "projects/{project_id}/serviceAccounts/{account}".format(
        project_id=project_id, account=account
    )
    try:
        service_account = iam.projects().serviceAccounts().get(name=full_name).execute()
    except errors.HttpError as e:
        if e.resp.status not in [403, 404]:
            # SkyPilot: added 403, which means the service account doesn't exist,
            # or not accessible by the current account, which is fine, as we do the
            # fallback in the caller.
            raise
        service_account = None

    return service_account


def _create_service_account(account_id, account_config, config, iam):
    project_id = config["provider"]["project_id"]

    service_account = (
        iam.projects()
        .serviceAccounts()
        .create(
            name="projects/{project_id}".format(project_id=project_id),
            body={
                "accountId": account_id,
                "serviceAccount": account_config,
            },
        )
        .execute()
    )

    return service_account


def _add_iam_policy_binding(service_account, policy, crm, iam):
    """Add new IAM roles for the service account."""
    project_id = service_account["projectId"]

    result = (
        crm.projects()
        .setIamPolicy(
            resource=project_id,
            body={
                "policy": policy,
            },
        )
        .execute()
    )

    return result


def _create_project_ssh_key_pair(project, public_key, ssh_user, compute):
    """Inserts an ssh-key into project commonInstanceMetadata"""

    key_parts = public_key.split(" ")

    # Sanity checks to make sure that the generated key matches expectation
    assert len(key_parts) == 2, key_parts
    assert key_parts[0] == "ssh-rsa", key_parts

    new_ssh_meta = "{ssh_user}:ssh-rsa {key_value} {ssh_user}".format(
        ssh_user=ssh_user, key_value=key_parts[1]
    )

    common_instance_info = project["commonInstanceMetadata"]
    items = common_instance_info.get("items", [])

    ssh_keys_i = next(
        (i for i, item in enumerate(items) if item["key"] == "ssh-keys"), None
    )

    if ssh_keys_i is None:
        items.append({"key": "ssh-keys", "value": new_ssh_meta})
    else:
        ssh_keys = items[ssh_keys_i]
        ssh_keys["value"] += "\n" + new_ssh_meta
        items[ssh_keys_i] = ssh_keys

    common_instance_info["items"] = items

    operation = (
        compute.projects()
        .setCommonInstanceMetadata(project=project["name"], body=common_instance_info)
        .execute()
    )

    response = wait_for_compute_global_operation(project["name"], operation, compute)

    return response
