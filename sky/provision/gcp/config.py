import copy
import json
import logging
import time

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient import discovery, errors

from sky.provision.gcp.node import MAX_POLLS, POLL_INTERVAL, GCPNodeType

logger = logging.getLogger(__name__)

VERSION = 'v1'
TPU_VERSION = 'v2alpha'  # change once v2 is stable

RAY = 'ray-autoscaler'
DEFAULT_SERVICE_ACCOUNT_ID = RAY + '-sa-' + VERSION
SERVICE_ACCOUNT_EMAIL_TEMPLATE = '{account_id}@{project_id}.iam.gserviceaccount.com'
DEFAULT_SERVICE_ACCOUNT_CONFIG = {
    'displayName': f'Ray Autoscaler Service Account ({VERSION})',
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


def get_node_type(node: dict) -> GCPNodeType:
    """Returns node type based on the keys in ``node``.

    This is a very simple check. If we have a ``machineType`` key,
    this is a Compute instance. If we don't have a ``machineType`` key,
    but we have ``acceleratorType``, this is a TPU. Otherwise, it's
    invalid and an exception is raised.

    This works for both node configs and API returned nodes.
    """

    if 'machineType' not in node and 'acceleratorType' not in node:
        raise ValueError(
            'Invalid node. For a Compute instance, "machineType" is '
            'required. '
            'For a TPU instance, "acceleratorType" and no "machineType" '
            'is required. '
            f'Got {list(node)}')

    if 'machineType' not in node and 'acceleratorType' in node:
        return GCPNodeType.TPU
    return GCPNodeType.COMPUTE


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

    def _create_credentials(creds):
        if provider_config.get(HAS_TPU_PROVIDER_FIELD, False):
            _create_tpu_optional = _create_tpu
        else:

            def _create_tpu_optional(_):
                return None

        # If gcp_credentials is None, then discovery.build will search for
        # credentials in the local environment.
        return _create_crm(creds), _create_iam(creds), _create_compute(
            creds), _create_tpu_optional(creds)

    gcp_credentials = provider_config.get('gcp_credentials')
    if gcp_credentials is None:
        logger.debug('gcp_credentials not found in cluster yaml file. '
                     'Falling back to GOOGLE_APPLICATION_CREDENTIALS '
                     'environment variable.')
        return _create_credentials(None)

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
    else:
        raise ValueError(f'Unknown GCP credential type: {cred_type}')

    return _create_credentials(credentials)


def bootstrap_gcp(config):
    config = copy.deepcopy(config)
    region = config['provider']['region']
    project_id = config['provider'].get('project_id')
    assert config['provider']['project_id'] is not None, (
        '"project_id" must be set in the "provider" section of the autoscaler'
        ' config. Notice that the project id must be globally unique.')
    assert 'ssh_private_key' in config['auth']

    node_config = config['node_config']

    crm, iam, compute, tpu = construct_clients_from_provider_config(
        config['provider'])

    _configure_project(project_id, crm)

    has_tpu = (get_node_type(node_config) == GCPNodeType.TPU)
    account_dict = _configure_iam_role(project_id, has_tpu, crm, iam)
    if has_tpu:
        # SKY: The API for TPU VM is slightly different from normal compute instances.
        # See https://cloud.google.com/tpu/docs/reference/rest/v2alpha1/projects.locations.nodes#Node
        account_dict['scope'] = account_dict['scopes']
        account_dict.pop('scopes')
        node_config['serviceAccount'] = account_dict
    else:
        node_config['serviceAccounts'] = [account_dict]

    # Rationale: avoid subnet lookup if the network is already
    # completely manually configured
    # networkInterfaces is compute, networkConfig is TPU
    if ('networkInterfaces' not in node_config and
            'networkConfig' not in node_config):
        default_interfaces = _configure_subnet(project_id, region, compute)
        # The not applicable key will be removed during node creation
        # compute
        if 'networkInterfaces' not in node_config:
            node_config['networkInterfaces'] = default_interfaces
        # TPU
        if 'networkConfig' not in node_config:
            node_config['networkConfig'] = default_interfaces[0]
            node_config['networkConfig'].pop('accessConfigs')
    return config


def _configure_project(project_id: str, crm):
    """Setup a Google Cloud Platform Project.

    Google Compute Platform organizes all the resources, such as storage
    buckets, users, and instances under projects. This is different from
    aws ec2 where everything is global.
    """
    try:
        project = crm.projects().get(projectId=project_id).execute()
    except errors.HttpError as e:
        if e.resp.status != 403:
            raise
        project = None

    if project is None:
        #  Project not found, try creating it
        operation = (crm.projects().create(body={
            'projectId': project_id,
            'name': project_id
        }).execute())
        _ = wait_for_crm_operation(operation, crm)
        # check the project is available
        project = crm.projects().get(projectId=project_id).execute()

    assert project is not None, 'Failed to create project'
    assert (project['lifecycleState'] == 'ACTIVE'
           ), 'Project status needs to be ACTIVE, got {}'.format(
               project['lifecycleState'])
    assert project_id == project['projectId']


def _configure_iam_role(project_id: str, has_tpu: bool, crm, iam):
    """Setup a gcp service account with IAM roles.

    Creates a gcp service acconut and binds IAM roles which allow it to control
    control storage/compute services. Specifically, the head node needs to have
    an IAM role that allows it to create further gce instances and store items
    in google cloud storage.

    TODO: Allow the name/id of the service account to be configured
    """
    # email is also the account name
    email = SERVICE_ACCOUNT_EMAIL_TEMPLATE.format(
        account_id=DEFAULT_SERVICE_ACCOUNT_ID,
        project_id=project_id,
    )

    full_name = f'projects/{project_id}/serviceAccounts/{email}'
    try:
        service_account = iam.projects().serviceAccounts().get(
            name=full_name).execute()
    except errors.HttpError as e:
        if e.resp.status != 404:
            raise
        service_account = None

    if service_account is None:
        logger.info(
            '_configure_iam_role: '
            f'Creating new service account {DEFAULT_SERVICE_ACCOUNT_ID}')

        service_account = (iam.projects().serviceAccounts().create(
            name=f'projects/{project_id}',
            body={
                'accountId': DEFAULT_SERVICE_ACCOUNT_ID,
                'serviceAccount': DEFAULT_SERVICE_ACCOUNT_CONFIG,
            },
        ).execute())

    assert service_account is not None, 'Failed to create service account'

    if has_tpu:
        roles = DEFAULT_SERVICE_ACCOUNT_ROLES + TPU_SERVICE_ACCOUNT_ROLES
    else:
        roles = DEFAULT_SERVICE_ACCOUNT_ROLES

    _add_iam_policy_binding(service_account, roles, crm)

    account_dict = {
        'email': service_account['email'],
        # NOTE: The amount of access is determined by the scope + IAM
        # role of the service account. Even if the cloud-platform scope
        # gives (scope) access to the whole cloud-platform, the service
        # account is limited by the IAM rights specified below.
        'scopes': ['https://www.googleapis.com/auth/cloud-platform']
    }
    return account_dict


def _configure_subnet(project_id: str, region: str, compute):
    """Pick a reasonable subnet if not specified by the config."""
    subnets = compute.subnetworks().list(
        project=project_id,
        region=region,
    ).execute()['items']

    if not subnets:
        raise NotImplementedError('Should be able to create subnet.')

    # TODO: make sure that we have usable subnet. Maybe call
    # compute.subnetworks().listUsable? For some reason it didn't
    # work out-of-the-box
    default_subnet = subnets[0]

    default_interfaces = [{
        'subnetwork': default_subnet['selfLink'],
        'accessConfigs': [{
            'name': 'External NAT',
            'type': 'ONE_TO_ONE_NAT',
        }],
    }]

    return default_interfaces


def _add_iam_policy_binding(service_account, roles, crm):
    """Add new IAM roles for the service account."""
    project_id = service_account['projectId']
    email = service_account['email']
    member_id = 'serviceAccount:' + email

    policy = crm.projects().getIamPolicy(resource=project_id, body={}).execute()

    already_configured = True
    for role in roles:
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
        return

    result = (crm.projects().setIamPolicy(
        resource=project_id,
        body={
            'policy': policy,
        },
    ).execute())

    return result
