"""GCP configuration bootstrapping."""
import copy
import json
import logging
import time
from typing import Dict, List, Set, Tuple

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient import discovery
from googleapiclient import errors

from sky.provision import common
from sky.provision.gcp import instance_utils
from sky.provision.gcp.constants import FIREWALL_RULES_REQUIRED
from sky.provision.gcp.constants import FIREWALL_RULES_TEMPLATE
from sky.provision.gcp.constants import MAX_POLLS
from sky.provision.gcp.constants import POLL_INTERVAL
from sky.provision.gcp.constants import SKYPILOT_VPC_NAME
from sky.provision.gcp.constants import TPU_MINIMAL_PERMISSIONS
from sky.provision.gcp.constants import VM_MINIMAL_PERMISSIONS
from sky.provision.gcp.constants import VPC_TEMPLATE

logger = logging.getLogger(__name__)

VERSION = 'v1'
TPU_VERSION = 'v2alpha'  # change once v2 is stable

RAY = 'ray-autoscaler'
DEFAULT_SERVICE_ACCOUNT_ID = RAY + '-sa-' + VERSION
SERVICE_ACCOUNT_EMAIL_TEMPLATE = '{account_id}@{project_id}.iam.gserviceaccount.com'
DEFAULT_SERVICE_ACCOUNT_CONFIG = {
    'displayName': f'Ray Autoscaler Service Account ({VERSION})',
}

SKYPILOT = 'skypilot'
SKYPILOT_SERVICE_ACCOUNT_ID = SKYPILOT + '-' + VERSION
SKYPILOT_SERVICE_ACCOUNT_EMAIL_TEMPLATE = (
    '{account_id}@{project_id}.iam.gserviceaccount.com')
SKYPILOT_SERVICE_ACCOUNT_CONFIG = {
    'displayName': f'SkyPilot Service Account ({VERSION})',
}

# Those roles will be always added.
# NOTE: `serviceAccountUser` allows the head node to create workers with
# a serviceAccount. `roleViewer` allows the head node to run bootstrap_gcp.
DEFAULT_SERVICE_ACCOUNT_ROLES = [
    'roles/storage.objectAdmin',
    'roles/compute.admin',
    'roles/iam.serviceAccountUser',
    'roles/iam.roleViewer',
]
# Those roles will only be added if there are TPU nodes defined in config.
TPU_SERVICE_ACCOUNT_ROLES = ['roles/tpu.admin']

# If there are TPU nodes in config, this field will be set
# to True in config['provider'].
HAS_TPU_PROVIDER_FIELD = '_has_tpus'

# NOTE: iam.serviceAccountUser allows the Head Node to create worker nodes
# with ServiceAccounts.


def wait_for_crm_operation(operation, crm):
    """Poll for cloud resource manager operation until finished."""
    logger.info('wait_for_crm_operation: '
                'Waiting for operation {} to finish...'.format(operation))

    for _ in range(MAX_POLLS):
        result = crm.operations().get(name=operation['name']).execute()
        if 'error' in result:
            raise Exception(result['error'])

        if 'done' in result and result['done']:
            logger.info('wait_for_crm_operation: Operation done.')
            break

        time.sleep(POLL_INTERVAL)

    return result


def wait_for_compute_global_operation(project_name, operation, compute):
    """Poll for global compute operation until finished."""
    logger.info('wait_for_compute_global_operation: '
                'Waiting for operation {} to finish...'.format(
                    operation['name']))

    for _ in range(MAX_POLLS):
        result = (compute.globalOperations().get(
            project=project_name,
            operation=operation['name'],
        ).execute())
        if 'error' in result:
            raise Exception(result['error'])

        if result['status'] == 'DONE':
            logger.info('wait_for_compute_global_operation: Operation done.')
            break

        time.sleep(POLL_INTERVAL)

    return result


def _create_crm(gcp_credentials=None):
    return discovery.build('cloudresourcemanager',
                           'v1',
                           credentials=gcp_credentials,
                           cache_discovery=False)


def _create_iam(gcp_credentials=None):
    return discovery.build('iam',
                           'v1',
                           credentials=gcp_credentials,
                           cache_discovery=False)


def _create_compute(gcp_credentials=None):
    return discovery.build('compute',
                           'v1',
                           credentials=gcp_credentials,
                           cache_discovery=False)


def _create_tpu(gcp_credentials=None):
    return discovery.build(
        'tpu',
        TPU_VERSION,
        credentials=gcp_credentials,
        cache_discovery=False,
        discoveryServiceUrl='https://tpu.googleapis.com/$discovery/rest',
    )


def construct_clients_from_provider_config(provider_config):
    """
    Attempt to fetch and parse the JSON GCP credentials from the provider
    config yaml file.

    tpu resource (the last element of the tuple) will be None if
    `_has_tpus` in provider config is not set or False.
    """
    gcp_credentials = provider_config.get('gcp_credentials')
    if gcp_credentials is None:
        logger.debug('gcp_credentials not found in cluster yaml file. '
                     'Falling back to GOOGLE_APPLICATION_CREDENTIALS '
                     'environment variable.')
        tpu_resource = (_create_tpu() if provider_config.get(
            HAS_TPU_PROVIDER_FIELD, False) else None)
        # If gcp_credentials is None, then discovery.build will search for
        # credentials in the local environment.
        return _create_crm(), _create_iam(), _create_compute(), tpu_resource

    assert ('type' in gcp_credentials
           ), 'gcp_credentials cluster yaml field missing "type" field.'
    assert ('credentials' in gcp_credentials
           ), 'gcp_credentials cluster yaml field missing "credentials" field.'

    cred_type = gcp_credentials['type']
    credentials_field = gcp_credentials['credentials']

    if cred_type == 'service_account':
        # If parsing the gcp_credentials failed, then the user likely made a
        # mistake in copying the credentials into the config yaml.
        try:
            service_account_info = json.loads(credentials_field)
        except json.decoder.JSONDecodeError:
            raise RuntimeError('gcp_credentials found in cluster yaml file but '
                               'formatted improperly.')
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info)
    elif cred_type == 'credentials_token':
        # Otherwise the credentials type must be credentials_token.
        credentials = OAuthCredentials(credentials_field)

    tpu_resource = (_create_tpu(credentials) if provider_config.get(
        HAS_TPU_PROVIDER_FIELD, False) else None)

    return (
        _create_crm(credentials),
        _create_iam(credentials),
        _create_compute(credentials),
        tpu_resource,
    )


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    # Check if we have any TPUs defined, and if so,
    # insert that information into the provider config
    if instance_utils.get_node_type(
            config.node_config) == instance_utils.GCPNodeType.TPU:
        config.provider_config[HAS_TPU_PROVIDER_FIELD] = True

    crm, iam, compute, tpu = construct_clients_from_provider_config(
        config.provider_config)

    # Setup a Google Cloud Platform Project.

    # Google Compute Platform organizes all the resources, such as storage
    # buckets, users, and instances under projects. This is different from
    # aws ec2 where everything is global.

    _configure_project(config.provider_config, crm)
    iam_role = _configure_iam_role(config, crm, iam)
    config.provider_config['iam_role'] = iam_role
    config = _configure_subnet(region, cluster_name, config, compute)

    return config


def _configure_project(provider_config, crm):
    """Setup a Google Cloud Platform Project.

    Google Compute Platform organizes all the resources, such as storage
    buckets, users, and instances under projects. This is different from
    aws ec2 where everything is global.
    """
    project_id = provider_config.get('project_id')
    assert project_id is not None, (
        '"project_id" must be set in the "provider" section of the autoscaler'
        ' config. Notice that the project id must be globally unique.')
    project = _get_project(project_id, crm)

    if project is None:
        #  Project not found, try creating it
        _create_project(project_id, crm)
        project = _get_project(project_id, crm)

    assert project is not None, 'Failed to create project'
    assert (project['lifecycleState'] == 'ACTIVE'
           ), 'Project status needs to be ACTIVE, got {}'.format(
               project['lifecycleState'])

    provider_config['project_id'] = project['projectId']


def _is_permission_satisfied(service_account, crm, iam, required_permissions,
                             required_roles):
    """Check if either of the roles or permissions are satisfied."""
    if service_account is None:
        return False, None

    project_id = service_account['projectId']
    email = service_account['email']

    member_id = 'serviceAccount:' + email

    required_permissions = set(required_permissions)
    policy = crm.projects().getIamPolicy(resource=project_id, body={}).execute()
    original_policy = copy.deepcopy(policy)
    already_configured = True

    logger.info(f'_configure_iam_role: Checking permissions for {email}...')

    # Check the roles first, as checking the permission
    # requires more API calls and permissions.
    for role in required_roles:
        role_exists = False
        for binding in policy['bindings']:
            if binding['role'] == role:
                if member_id not in binding['members']:
                    binding['members'].append(member_id)
                    already_configured = False
                role_exists = True

        if not role_exists:
            already_configured = False
            policy['bindings'].append({
                'members': [member_id],
                'role': role,
            })

    if already_configured:
        # In some managed environments, an admin needs to grant the
        # roles, so only call setIamPolicy if needed.
        return True, policy

    for binding in original_policy['bindings']:
        if member_id in binding['members']:
            role = binding['role']
            try:
                role_definition = iam.projects().roles().get(
                    name=role).execute()
            except TypeError as e:
                if 'does not match the pattern' in str(e):
                    logger.info('_configure_iam_role: fail to check permission '
                                f'for built-in role {role}. skipped.')
                    permissions = []
                else:
                    raise
            else:
                permissions = role_definition['includedPermissions']
            required_permissions -= set(permissions)
        if not required_permissions:
            break
    if not required_permissions:
        # All required permissions are already granted.
        return True, policy
    logger.info(
        f'_configure_iam_role: missing permisisons {required_permissions}')

    return False, policy


def _configure_iam_role(config: common.ProvisionConfig, crm, iam):
    """Setup a gcp service account with IAM roles.

    Creates a gcp service acconut and binds IAM roles which allow it to control
    control storage/compute services. Specifically, the head node needs to have
    an IAM role that allows it to create further gce instances and store items
    in google cloud storage.

    TODO: Allow the name/id of the service account to be configured
    """
    project_id = config.provider_config['project_id']
    email = SKYPILOT_SERVICE_ACCOUNT_EMAIL_TEMPLATE.format(
        account_id=SKYPILOT_SERVICE_ACCOUNT_ID,
        project_id=project_id,
    )
    service_account = _get_service_account(email, project_id, iam)

    permissions = VM_MINIMAL_PERMISSIONS
    roles = DEFAULT_SERVICE_ACCOUNT_ROLES
    if config.provider_config.get(HAS_TPU_PROVIDER_FIELD, False):
        roles = DEFAULT_SERVICE_ACCOUNT_ROLES + TPU_SERVICE_ACCOUNT_ROLES
        permissions = VM_MINIMAL_PERMISSIONS + TPU_MINIMAL_PERMISSIONS

    satisfied, policy = _is_permission_satisfied(service_account, crm, iam,
                                                 permissions, roles)

    if not satisfied:
        # SkyPilot: Fallback to the old ray service account name for
        # backwards compatibility. Users using GCP before #2112 have
        # the old service account setup setup in their GCP project,
        # and the user may not have the permissions to create the
        # new service account. This is to ensure that the old service
        # account is still usable.
        email = SERVICE_ACCOUNT_EMAIL_TEMPLATE.format(
            account_id=DEFAULT_SERVICE_ACCOUNT_ID,
            project_id=project_id,
        )
        logger.info(f'_configure_iam_role: Fallback to service account {email}')

        ray_service_account = _get_service_account(email, project_id, iam)
        ray_satisfied, _ = _is_permission_satisfied(ray_service_account, crm,
                                                    iam, permissions, roles)
        logger.info(
            '_configure_iam_role: '
            f'Fallback to service account {email} succeeded? {ray_satisfied}')

        if ray_satisfied:
            service_account = ray_service_account
            satisfied = ray_satisfied
        elif service_account is None:
            logger.info('_configure_iam_role: '
                        'Creating new service account {}'.format(
                            SKYPILOT_SERVICE_ACCOUNT_ID))
            # SkyPilot: a GCP user without the permission to create a service
            # account will fail here.
            service_account = _create_service_account(
                SKYPILOT_SERVICE_ACCOUNT_ID,
                SKYPILOT_SERVICE_ACCOUNT_CONFIG,
                project_id,
                iam,
            )
            satisfied, policy = _is_permission_satisfied(
                service_account, crm, iam, permissions, roles)

    assert service_account is not None, 'Failed to create service account'

    if not satisfied:
        logger.info('_configure_iam_role: '
                    f'Adding roles to service account {email}...')
        _add_iam_policy_binding(service_account, policy, crm, iam)

    account_dict = {
        'email': service_account['email'],
        # NOTE: The amount of access is determined by the scope + IAM
        # role of the service account. Even if the cloud-platform scope
        # gives (scope) access to the whole cloud-platform, the service
        # account is limited by the IAM rights specified below.
        'scopes': ['https://www.googleapis.com/auth/cloud-platform'],
    }
    if instance_utils.get_node_type(
            config.node_config) == instance_utils.GCPNodeType.TPU:
        # SKY: The API for TPU VM is slightly different from normal compute instances.
        # See https://cloud.google.com/tpu/docs/reference/rest/v2alpha1/projects.locations.nodes#Node
        account_dict['scope'] = account_dict['scopes']
        account_dict.pop('scopes')
        iam_role = {'serviceAccount': account_dict}
    else:
        iam_role = {'serviceAccounts': [account_dict]}

    return iam_role


def _check_firewall_rules(cluster_name: str, vpc_name: str, project_id: str,
                          compute):
    """Check if the firewall rules in the VPC are sufficient."""
    required_rules = FIREWALL_RULES_REQUIRED.copy()

    operation = compute.networks().getEffectiveFirewalls(project=project_id,
                                                         network=vpc_name)
    response = operation.execute()
    if len(response) == 0:
        return False
    effective_rules = response['firewalls']

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
            tags = rule.get('targetTags', None)
            if tags is not None:
                if len(tags) != 1:
                    continue
                if tags[0] != cluster_name:
                    continue
            direction = rule.get('direction', '')
            sources = rule.get('sourceRanges', [])
            allowed = rule.get('allowed', [])
            for source in sources:
                key = (direction, source)
                source2allowed_list[key] = source2allowed_list.get(key,
                                                                   []) + allowed
        for direction_source, allowed_list in source2allowed_list.items():
            source2rules[direction_source] = {}
            for allowed in allowed_list:
                # Example of port_list: ['20', '50-60']
                # If list is empty, it means all ports
                port_list = allowed.get('ports', [])
                port_set = set()
                if port_list == []:
                    port_set.update(set(range(1, 65536)))
                else:
                    for port_range in port_list:
                        parse_ports = port_range.split('-')
                        if len(parse_ports) == 1:
                            port_set.add(int(parse_ports[0]))
                        else:
                            assert (
                                len(parse_ports) == 2
                            ), f'Failed to parse the port range: {port_range}'
                            port_set.update(
                                set(
                                    range(int(parse_ports[0]),
                                          int(parse_ports[1]) + 1)))
                if allowed['IPProtocol'] not in source2rules[direction_source]:
                    source2rules[direction_source][
                        allowed['IPProtocol']] = set()
                source2rules[direction_source][allowed['IPProtocol']].update(
                    port_set)
        return source2rules

    effective_rules = _merge_and_refine_rule(effective_rules)
    required_rules = _merge_and_refine_rule(required_rules)

    for direction_source, allowed_req in required_rules.items():
        if direction_source not in effective_rules:
            return False
        allowed_eff = effective_rules[direction_source]
        # Special case: 'all' means allowing all traffic
        if 'all' in allowed_eff:
            continue
        # Check if the required ports are a subset of the effective ports
        for protocol, ports_req in allowed_req.items():
            ports_eff = allowed_eff.get(protocol, set())
            if not ports_req.issubset(ports_eff):
                return False
    return True


def _create_rules(project_id: str, compute, rules, VPC_NAME):
    opertaions = []
    for rule in rules:
        # Query firewall rule by its name (unique in a project).
        # If the rule already exists, delete it first.
        rule_name = rule['name'].format(VPC_NAME=VPC_NAME)
        rule_list = _list_firewall_rules(project_id,
                                         compute,
                                         filter=f'(name={rule_name})')
        if len(rule_list) > 0:
            _delete_firewall_rule(project_id, compute, rule_name)

        body = rule.copy()
        body['name'] = body['name'].format(VPC_NAME=VPC_NAME)
        body['network'] = body['network'].format(PROJ_ID=project_id,
                                                 VPC_NAME=VPC_NAME)
        body['selfLink'] = body['selfLink'].format(PROJ_ID=project_id,
                                                   VPC_NAME=VPC_NAME)
        op = compute.firewalls().insert(project=project_id, body=body).execute()
        opertaions.append(op)
    for op in opertaions:
        wait_for_compute_global_operation(project_id, op, compute)


def get_usable_vpc(cluster_name: str, config: common.ProvisionConfig):
    """Return a usable VPC.

    If not found, create a new one with sufficient firewall rules.
    """
    project_id = config.provider_config['project_id']
    _, _, compute, _ = construct_clients_from_provider_config(
        config.provider_config)

    # For backward compatibility, reuse the VPC if the VM is launched.
    instance_dict = instance_utils.GCPComputeInstance.filter(
        project_id,
        config.provider_config['availability_zone'],
        label_filters=None,
        status_filters=None)

    if instance_dict:
        instance_metadata = list(instance_dict.values())[0]
        netInterfaces = instance_metadata.get('networkInterfaces', [])
        if len(netInterfaces) > 0:
            vpc_name = netInterfaces[0]['network'].split('/')[-1]
            return vpc_name

    vpcnets_all = _list_vpcnets(project_id, compute)

    usable_vpc_name = None
    for vpc in vpcnets_all:
        if _check_firewall_rules(cluster_name, vpc['name'], project_id,
                                 compute):
            usable_vpc_name = vpc['name']
            break

    if usable_vpc_name is None:
        logger.info(f'Creating a default VPC network, {SKYPILOT_VPC_NAME}...')

        # Create a SkyPilot VPC network if it doesn't exist
        vpc_list = _list_vpcnets(project_id,
                                 compute,
                                 filter=f'name={SKYPILOT_VPC_NAME}')
        if len(vpc_list) == 0:
            body = VPC_TEMPLATE.copy()
            body['name'] = body['name'].format(VPC_NAME=SKYPILOT_VPC_NAME)
            body['selfLink'] = body['selfLink'].format(
                PROJ_ID=project_id, VPC_NAME=SKYPILOT_VPC_NAME)
            _create_vpcnet(project_id, compute, body)

        _create_rules(project_id, compute, FIREWALL_RULES_TEMPLATE,
                      SKYPILOT_VPC_NAME)

        usable_vpc_name = SKYPILOT_VPC_NAME
        logger.info(f'A VPC network {SKYPILOT_VPC_NAME} created.')

    return usable_vpc_name


def _configure_subnet(region: str, cluster_name: str,
                      config: common.ProvisionConfig, compute):
    """Pick a reasonable subnet if not specified by the config."""
    node_config = config.node_config
    # Rationale: avoid subnet lookup if the network is already
    # completely manually configured

    # networkInterfaces is compute, networkConfig is TPU
    if 'networkInterfaces' in node_config or 'networkConfig' in node_config:
        return config

    # SkyPilot: make sure there's a usable VPC
    usable_vpc_name = get_usable_vpc(cluster_name, config)
    subnets = _list_subnets(config.provider_config['project_id'],
                            region,
                            compute,
                            filter=f'(name="{usable_vpc_name}")')
    default_subnet = subnets[0]

    default_interfaces = [{
        'subnetwork': default_subnet['selfLink'],
        'accessConfigs': [{
            'name': 'External NAT',
            'type': 'ONE_TO_ONE_NAT',
        }],
    }]

    # The not applicable key will be removed during node creation

    # compute
    if 'networkInterfaces' not in node_config:
        node_config['networkInterfaces'] = copy.deepcopy(default_interfaces)
    # TPU
    if 'networkConfig' not in node_config:
        node_config['networkConfig'] = copy.deepcopy(default_interfaces)[0]
        node_config['networkConfig'].pop('accessConfigs')

    return config


def _delete_firewall_rule(project_id: str, compute, name):
    operation = (compute.firewalls().delete(project=project_id,
                                            firewall=name).execute())
    response = wait_for_compute_global_operation(project_id, operation, compute)
    return response


def _list_firewall_rules(project_id, compute, filter=None):
    response = (compute.firewalls().list(
        project=project_id,
        filter=filter,
    ).execute())
    return response['items'] if 'items' in response else []


def _create_vpcnet(project_id: str, compute, body):
    operation = (compute.networks().insert(project=project_id,
                                           body=body).execute())
    response = wait_for_compute_global_operation(project_id, operation, compute)
    return response


def _list_vpcnets(project_id: str, compute, filter=None):
    response = (compute.networks().list(
        project=project_id,
        filter=filter,
    ).execute())

    return response['items'] if 'items' in response else []


def _list_subnets(project_id: str, region: str, compute, filter=None):
    response = (compute.subnetworks().list(
        project=project_id,
        region=region,
        filter=filter,
    ).execute())

    return response['items'] if 'items' in response else []


def _get_project(project_id: str, crm):
    try:
        project = crm.projects().get(projectId=project_id).execute()
    except errors.HttpError as e:
        if e.resp.status != 403:
            raise
        project = None

    return project


def _create_project(project_id: str, crm):
    operation = (crm.projects().create(body={
        'projectId': project_id,
        'name': project_id
    }).execute())

    result = wait_for_crm_operation(operation, crm)

    return result


def _get_service_account(account: str, project_id: str, iam):
    full_name = 'projects/{project_id}/serviceAccounts/{account}'.format(
        project_id=project_id, account=account)
    try:
        service_account = iam.projects().serviceAccounts().get(
            name=full_name).execute()
    except errors.HttpError as e:
        if e.resp.status not in [403, 404]:
            # SkyPilot: added 403, which means the service account doesn't exist,
            # or not accessible by the current account, which is fine, as we do the
            # fallback in the caller.
            raise
        service_account = None

    return service_account


def _create_service_account(account_id: str, account_config, project_id: str,
                            iam):
    service_account = (iam.projects().serviceAccounts().create(
        name='projects/{project_id}'.format(project_id=project_id),
        body={
            'accountId': account_id,
            'serviceAccount': account_config,
        },
    ).execute())

    return service_account


def _add_iam_policy_binding(service_account, policy, crm, iam):
    """Add new IAM roles for the service account."""
    project_id = service_account['projectId']

    result = (crm.projects().setIamPolicy(
        resource=project_id,
        body={
            'policy': policy,
        },
    ).execute())

    return result
