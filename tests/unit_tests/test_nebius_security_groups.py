"""Unit tests for Nebius security group management.

Covers the move from host-level UFW (PR #8627 / hotfix #9562) to native
Nebius security groups: bootstrap_instances creates+seeds the SG,
open_ports adds ingress rules idempotently, cleanup_ports deletes the SG
on teardown with retry-on-dependency, and the legacy-cluster warn path.
"""
# pylint: disable=line-too-long
import importlib.util
from unittest import mock

import pytest

from sky.adaptors import nebius as nebius_adaptor
from sky.provision import common
from sky.provision.nebius import config as nebius_config
from sky.provision.nebius import instance as nebius_instance
from sky.provision.nebius import utils as nebius_utils

# CI's "Unit Tests" job installs only `[kubernetes,aws,gcp,azure]` extras
# (`.github/workflows/pytest.yml`) — nebius is not in that list. Tests
# that exercise the real Nebius SDK (proto construction or utils helpers
# that build SecurityGroup/Rule requests) are gated on the SDK being
# importable. Tests that fully mock at the helper boundary run regardless.
_HAS_NEBIUS_SDK = importlib.util.find_spec('nebius') is not None
nebius_sdk_required = pytest.mark.skipif(
    not _HAS_NEBIUS_SDK,
    reason='Nebius SDK not installed in this environment',
)


def _fake_rule(access, protocol, ingress=None, egress=None):
    """Build a fake SecurityRule object with `.spec` for dedup tests."""
    spec = mock.MagicMock()
    spec.access = access
    spec.protocol = protocol
    spec.s_match = mock.MagicMock()
    if ingress is not None:
        spec.s_match.name = 'ingress'
        spec.ingress = ingress
        spec.egress = None
    else:
        spec.s_match.name = 'egress'
        spec.egress = egress
        spec.ingress = None
    rule = mock.MagicMock()
    rule.spec = spec
    return rule


def _fake_ingress(source_cidrs=None,
                  destination_ports=None,
                  source_security_group_id=None):
    ing = mock.MagicMock()
    ing.source_cidrs = source_cidrs or []
    ing.destination_ports = destination_ports or []
    ing.source_security_group_id = source_security_group_id
    return ing


def _fake_egress(destination_cidrs=None, destination_ports=None):
    eg = mock.MagicMock()
    eg.destination_cidrs = destination_cidrs or []
    eg.destination_ports = destination_ports or []
    eg.destination_security_group_id = None
    return eg


# --- bootstrap_instances ----------------------------------------------------


def test_bootstrap_creates_sg_when_missing():
    cfg = common.ProvisionConfig(
        provider_config={'region': 'eu-north1'},
        authentication_config={},
        docker_config={},
        node_config={},
        count=1,
        tags={},
        resume_stopped_nodes=True,
        ports_to_open_on_launch=None,
    )
    with mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_subnet_id',
                           return_value='subnet-1'), \
         mock.patch.object(nebius_utils, 'get_network_id_from_subnet',
                           return_value='net-1'), \
         mock.patch.object(nebius_utils, 'get_or_create_security_group',
                           return_value='sg-new') as mock_create, \
         mock.patch.object(nebius_utils, 'ensure_default_sg_rules') as mock_rules, \
         mock.patch.object(nebius_utils, 'list_instances', return_value={}):
        out = nebius_config.bootstrap_instances('eu-north1', 'mycluster', cfg)

    expected_name = nebius_utils.SECURITY_GROUP_TEMPLATE.format('mycluster')
    mock_create.assert_called_once_with('proj-abc', expected_name, 'net-1')
    mock_rules.assert_called_once_with('sg-new')
    assert out.provider_config['security_group']['GroupId'] == 'sg-new'
    assert out.provider_config['security_group']['GroupName'] == expected_name
    assert out.provider_config['security_group']['ManagedBySkyPilot'] is True
    assert out.node_config['SecurityGroupIds'] == ['sg-new']


def test_bootstrap_warns_for_legacy_instances():
    cfg = common.ProvisionConfig(
        provider_config={'region': 'eu-north1'},
        authentication_config={},
        docker_config={},
        node_config={},
        count=2,
        tags={},
        resume_stopped_nodes=True,
        ports_to_open_on_launch=None,
    )
    legacy_instances = {
        'i-old': {
            'name': 'mycluster-aaaa-head',
            'status': 'RUNNING',
            'security_group_ids': [],  # no SG attached — legacy
        },
    }
    with mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_subnet_id',
                           return_value='subnet-1'), \
         mock.patch.object(nebius_utils, 'get_network_id_from_subnet',
                           return_value='net-1'), \
         mock.patch.object(nebius_utils, 'get_or_create_security_group',
                           return_value='sg-new'), \
         mock.patch.object(nebius_utils, 'ensure_default_sg_rules'), \
         mock.patch.object(nebius_utils, 'list_instances',
                           return_value=legacy_instances), \
         mock.patch.object(nebius_config, 'logger') as mock_logger:
        out = nebius_config.bootstrap_instances('eu-north1', 'mycluster', cfg)

    warn_calls = [c.args[0] for c in mock_logger.warning.call_args_list]
    assert any('pre-existing instance' in m for m in warn_calls)
    assert out.provider_config['security_group']['GroupId'] == 'sg-new'


# --- open_ports / cleanup_ports --------------------------------------------


def test_open_ports_resolves_sg_by_name_and_adds_ports():
    with mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_security_group_by_name',
                           return_value='sg-1') as mock_lookup, \
         mock.patch.object(nebius_utils, 'add_ingress_tcp_ports') as mock_add:
        nebius_instance.open_ports(
            'mycluster',
            ['8080', '9000-9002'],
            provider_config={'region': 'eu-north1'},
        )

    mock_lookup.assert_called_once_with(
        'proj-abc', nebius_utils.SECURITY_GROUP_TEMPLATE.format('mycluster'))
    args, _ = mock_add.call_args
    assert args[0] == 'sg-1'
    assert args[1] == {8080, 9000, 9001, 9002}


def test_open_ports_legacy_cluster_logs_and_returns():
    with mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_security_group_by_name',
                           return_value=None), \
         mock.patch.object(nebius_utils, 'add_ingress_tcp_ports') as mock_add, \
         mock.patch.object(nebius_instance, 'logger') as mock_logger:
        nebius_instance.open_ports(
            'legacy',
            ['8080'],
            provider_config={'region': 'eu-north1'},
        )

    mock_add.assert_not_called()
    warn_calls = [c.args[0] for c in mock_logger.warning.call_args_list]
    assert any('not found' in m for m in warn_calls)


def test_open_ports_skips_for_byo_sg():
    """BYO SG: open_ports must NOT mutate the user-managed SG. The user
    owns the rule set; we already warned at launch time."""
    with mock.patch.object(nebius_utils, 'get_project_by_region') as mock_proj, \
         mock.patch.object(nebius_utils, 'get_security_group_by_name') as mock_lookup, \
         mock.patch.object(nebius_utils, 'add_ingress_tcp_ports') as mock_add, \
         mock.patch.object(nebius_instance, 'logger') as mock_logger:
        nebius_instance.open_ports(
            'mycluster',
            ['8080'],
            provider_config={
                'region': 'eu-north1',
                'security_group': {
                    'GroupName': 'my-byo',
                    'ManagedBySkyPilot': False,
                },
            },
        )

    mock_proj.assert_not_called()
    mock_lookup.assert_not_called()
    mock_add.assert_not_called()
    info_calls = [c.args[0] for c in mock_logger.info.call_args_list]
    assert any('user-managed (BYO) security group' in m for m in info_calls)


def test_cleanup_ports_is_noop():
    """SG deletion is the VM-lifecycle concern; cleanup_ports must not
    touch the SG (which is still attached to live VMs at this point in
    most invocation paths)."""
    with mock.patch.object(nebius_utils, 'delete_security_group') as mock_del, \
         mock.patch.object(nebius_utils, 'get_security_group_by_name') as mock_lookup:
        nebius_instance.cleanup_ports(
            'mycluster',
            ['8080'],
            provider_config={'region': 'eu-north1'},
        )
    mock_del.assert_not_called()
    mock_lookup.assert_not_called()


def test_terminate_instances_deletes_sg_on_full_teardown():
    """SG deletion belongs to terminate_instances when worker_only=False."""
    with mock.patch.object(nebius_instance, '_filter_instances',
                           return_value={}), \
         mock.patch.object(nebius_utils, 'delete_cluster'), \
         mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_security_group_by_name',
                           return_value='sg-2'), \
         mock.patch.object(nebius_utils, 'delete_security_group') as mock_del:
        nebius_instance.terminate_instances(
            'mycluster',
            provider_config={'region': 'eu-north1'},
            worker_only=False,
        )
    mock_del.assert_called_once_with('sg-2')


def test_terminate_instances_skips_sg_delete_for_worker_only():
    """Partial scale-down must not delete the SG (head still uses it)."""
    with mock.patch.object(nebius_instance, '_filter_instances',
                           return_value={}), \
         mock.patch.object(nebius_utils, 'delete_cluster'), \
         mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_security_group_by_name') as mock_lookup, \
         mock.patch.object(nebius_utils, 'delete_security_group') as mock_del:
        nebius_instance.terminate_instances(
            'mycluster',
            provider_config={'region': 'eu-north1'},
            worker_only=True,
        )
    mock_lookup.assert_not_called()
    mock_del.assert_not_called()


def test_terminate_instances_noop_for_legacy_cluster_sg():
    """If no SG exists for the cluster (pre-PR launch), terminate cleanly
    skips the SG-delete branch."""
    with mock.patch.object(nebius_instance, '_filter_instances',
                           return_value={}), \
         mock.patch.object(nebius_utils, 'delete_cluster'), \
         mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_security_group_by_name',
                           return_value=None), \
         mock.patch.object(nebius_utils, 'delete_security_group') as mock_del:
        nebius_instance.terminate_instances(
            'legacy',
            provider_config={'region': 'eu-north1'},
            worker_only=False,
        )
    mock_del.assert_not_called()


# --- BYO security group (nebius.security_group_name config) ----------------


def _make_byo_provision_config(group_name, managed=False):
    """Helper: a ProvisionConfig with a templated BYO `security_group` block.

    `managed` is a Python bool, matching what reaches the provisioner
    after YAML deserialization of the rendered Ray YAML.
    """
    return common.ProvisionConfig(
        provider_config={
            'region': 'eu-north1',
            'security_group': {
                'GroupName': group_name,
                'ManagedBySkyPilot': managed,
            },
        },
        authentication_config={},
        docker_config={},
        node_config={},
        count=1,
        tags={},
        resume_stopped_nodes=True,
        ports_to_open_on_launch=None,
    )


def test_bootstrap_byo_sg_lookup_succeeds():
    """BYO path: SG exists, network matches, has rules → no create call."""
    cfg = _make_byo_provision_config('my-byo')
    with mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_subnet_id',
                           return_value='subnet-1'), \
         mock.patch.object(nebius_utils, 'get_network_id_from_subnet',
                           return_value='net-1'), \
         mock.patch.object(nebius_utils,
                           'get_security_group_id_and_network_by_name',
                           return_value=('sg-byo', 'net-1')) as mock_lookup, \
         mock.patch.object(nebius_utils, 'list_security_rules',
                           return_value=[mock.MagicMock()]) as mock_list, \
         mock.patch.object(nebius_utils, 'ensure_default_sg_rules') as mock_seed, \
         mock.patch.object(nebius_utils, 'get_or_create_security_group') as mock_create, \
         mock.patch.object(nebius_utils, 'list_instances', return_value={}):
        out = nebius_config.bootstrap_instances('eu-north1', 'mycluster', cfg)

    mock_create.assert_not_called()  # BYO: never create
    mock_lookup.assert_called_once_with('proj-abc', 'my-byo')
    mock_list.assert_called_once_with('sg-byo')
    mock_seed.assert_not_called()  # BYO never seeds
    assert out.provider_config['security_group']['GroupId'] == 'sg-byo'
    assert out.provider_config['security_group']['GroupName'] == 'my-byo'
    assert out.provider_config['security_group']['ManagedBySkyPilot'] is False
    assert out.node_config['SecurityGroupIds'] == ['sg-byo']


def test_bootstrap_byo_sg_empty_raises():
    """BYO SG with zero rules must error (we don't silently seed defaults
    into a user-managed SG — diverges from AWS by design)."""
    cfg = _make_byo_provision_config('empty-byo')
    with mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_subnet_id',
                           return_value='subnet-1'), \
         mock.patch.object(nebius_utils, 'get_network_id_from_subnet',
                           return_value='net-1'), \
         mock.patch.object(nebius_utils,
                           'get_security_group_id_and_network_by_name',
                           return_value=('sg-byo', 'net-1')), \
         mock.patch.object(nebius_utils, 'list_security_rules',
                           return_value=[]), \
         mock.patch.object(nebius_utils, 'ensure_default_sg_rules') as mock_seed:
        with pytest.raises(ValueError, match='no rules'):
            nebius_config.bootstrap_instances('eu-north1', 'mycluster', cfg)
    mock_seed.assert_not_called()


def test_bootstrap_byo_sg_missing_raises():
    """BYO SG that doesn't exist: clear ValueError naming the SG."""
    cfg = _make_byo_provision_config('does-not-exist')
    with mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_subnet_id',
                           return_value='subnet-1'), \
         mock.patch.object(nebius_utils, 'get_network_id_from_subnet',
                           return_value='net-1'), \
         mock.patch.object(nebius_utils,
                           'get_security_group_id_and_network_by_name',
                           return_value=None):
        with pytest.raises(ValueError, match='does-not-exist'):
            nebius_config.bootstrap_instances('eu-north1', 'mycluster', cfg)


def test_bootstrap_byo_sg_wrong_network_raises():
    """BYO SG in a different network: clear ValueError with both networks
    + remediation hints."""
    cfg = _make_byo_provision_config('wrong-net-byo')
    with mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_subnet_id',
                           return_value='subnet-1'), \
         mock.patch.object(nebius_utils, 'get_network_id_from_subnet',
                           return_value='net-cluster'), \
         mock.patch.object(nebius_utils,
                           'get_security_group_id_and_network_by_name',
                           return_value=('sg-byo', 'net-different')):
        with pytest.raises(ValueError) as exc_info:
            nebius_config.bootstrap_instances('eu-north1', 'mycluster', cfg)
    msg = str(exc_info.value)
    assert 'wrong-net-byo' in msg
    assert 'net-different' in msg
    assert 'net-cluster' in msg
    assert 'remove `nebius.security_group_name`' in msg


def test_terminate_skips_sg_delete_when_byo_bool_false():
    """ManagedBySkyPilot=False (bool form) must also skip SG delete."""
    with mock.patch.object(nebius_instance, '_filter_instances',
                           return_value={}), \
         mock.patch.object(nebius_utils, 'delete_cluster'), \
         mock.patch.object(nebius_utils, 'get_project_by_region',
                           return_value='proj-abc'), \
         mock.patch.object(nebius_utils, 'get_security_group_by_name') as mock_lookup, \
         mock.patch.object(nebius_utils, 'delete_security_group') as mock_del:
        nebius_instance.terminate_instances(
            'mycluster',
            provider_config={
                'region': 'eu-north1',
                'security_group': {
                    'GroupName': 'my-byo',
                    'ManagedBySkyPilot': False,
                },
            },
            worker_only=False,
        )
    mock_lookup.assert_not_called()
    mock_del.assert_not_called()


# --- add_ingress_tcp_ports (dedup) -----------------------------------------


@nebius_sdk_required
def test_add_ingress_tcp_ports_dedupes_against_existing():
    """Re-running with the same ports must issue zero new rules."""

    vpc = nebius_adaptor.vpc()
    existing = [
        _fake_rule(
            access=int(vpc.RuleAccessAction.ALLOW),
            protocol=int(vpc.RuleProtocol.TCP),
            ingress=_fake_ingress(source_cidrs=['0.0.0.0/0'],
                                  destination_ports=[8080]),
        ),
    ]
    with mock.patch.object(nebius_utils, 'list_security_rules',
                           return_value=existing), \
         mock.patch.object(nebius_utils, '_create_security_rule') as mock_create:
        nebius_utils.add_ingress_tcp_ports('sg-1', {8080})

    mock_create.assert_not_called()


@nebius_sdk_required
def test_add_ingress_tcp_ports_batches_to_eight_per_rule():

    vpc = nebius_adaptor.vpc()
    with mock.patch.object(nebius_utils, 'list_security_rules',
                           return_value=[]), \
         mock.patch.object(nebius_utils, '_create_security_rule') as mock_create:
        nebius_utils.add_ingress_tcp_ports('sg-1', set(range(8000, 8020)))

    # 20 ports, 8 per rule → 3 rules.
    assert mock_create.call_count == 3
    # Each rule's spec has at most 8 ports.
    for call in mock_create.call_args_list:
        spec = call.args[1]
        assert len(spec.ingress.destination_ports) <= 8
        assert int(spec.access) == int(vpc.RuleAccessAction.ALLOW)
        assert int(spec.protocol) == int(vpc.RuleProtocol.TCP)
        assert list(spec.ingress.source_cidrs) == ['0.0.0.0/0']


# --- ensure_default_sg_rules -----------------------------------------------


@nebius_sdk_required
def test_ensure_default_sg_rules_creates_all_when_empty():
    with mock.patch.object(nebius_utils, 'list_security_rules',
                           return_value=[]), \
         mock.patch.object(nebius_utils, '_create_security_rule') as mock_create:
        nebius_utils.ensure_default_sg_rules('sg-1')

    # Default rule set: intra-cluster + ssh22 + ssh10022 + egress-all.
    assert mock_create.call_count == 4


@nebius_sdk_required
def test_ensure_default_sg_rules_idempotent_when_all_present():
    """Second call with all rules already present must add zero."""

    vpc = nebius_adaptor.vpc()
    existing = [
        _fake_rule(
            access=int(vpc.RuleAccessAction.ALLOW),
            protocol=int(vpc.RuleProtocol.ANY),
            ingress=_fake_ingress(source_security_group_id='sg-1'),
        ),
        _fake_rule(
            access=int(vpc.RuleAccessAction.ALLOW),
            protocol=int(vpc.RuleProtocol.TCP),
            ingress=_fake_ingress(source_cidrs=['0.0.0.0/0'],
                                  destination_ports=[22]),
        ),
        _fake_rule(
            access=int(vpc.RuleAccessAction.ALLOW),
            protocol=int(vpc.RuleProtocol.TCP),
            ingress=_fake_ingress(source_cidrs=['0.0.0.0/0'],
                                  destination_ports=[10022]),
        ),
        _fake_rule(
            access=int(vpc.RuleAccessAction.ALLOW),
            protocol=int(vpc.RuleProtocol.ANY),
            egress=_fake_egress(destination_cidrs=['0.0.0.0/0']),
        ),
    ]
    with mock.patch.object(nebius_utils, 'list_security_rules',
                           return_value=existing), \
         mock.patch.object(nebius_utils, '_create_security_rule') as mock_create:
        nebius_utils.ensure_default_sg_rules('sg-1')

    mock_create.assert_not_called()


# --- delete_security_group -------------------------------------------------


@nebius_sdk_required
def test_delete_sg_retries_on_dependency_then_succeeds():
    """First SG-delete attempt fails (VMs still attached), retry succeeds."""
    sg_delete_attempts = {'n': 0}

    def fake_sync_call(*_args, **_kwargs):
        # All sync_call invocations from delete_security_group are SG
        # deletes (rule listing/deletion are mocked separately below).
        sg_delete_attempts['n'] += 1
        if sg_delete_attempts['n'] == 1:
            err_cls = nebius_adaptor.request_error()
            status = mock.MagicMock()
            status.__str__ = lambda self: 'FAILED_PRECONDITION: in use by VM'
            raise err_cls(status)
        return None  # second attempt succeeds

    with mock.patch.object(nebius_utils, 'list_security_rules',
                           return_value=[]), \
         mock.patch('sky.adaptors.nebius.sync_call', side_effect=fake_sync_call), \
         mock.patch('sky.adaptors.nebius.sdk'), \
         mock.patch('time.sleep'):
        nebius_utils.delete_security_group('sg-1')

    assert sg_delete_attempts['n'] == 2  # one retry


@nebius_sdk_required
def test_delete_sg_swallows_not_found():
    """If the SG is already gone, return cleanly."""

    def fake_sync_call(*_args, **_kwargs):
        err_cls = nebius_adaptor.request_error()
        status = mock.MagicMock()
        status.__str__ = lambda self: 'NOT_FOUND: no such security group'
        raise err_cls(status)

    with mock.patch.object(nebius_utils, 'list_security_rules',
                           return_value=[]), \
         mock.patch('sky.adaptors.nebius.sync_call', side_effect=fake_sync_call), \
         mock.patch('sky.adaptors.nebius.sdk'):
        nebius_utils.delete_security_group('sg-gone')


@nebius_sdk_required
def test_delete_sg_logs_warning_on_retry_exhaustion():
    """After max attempts, log warning and return rather than raise."""

    def always_fail(*_args, **_kwargs):
        err_cls = nebius_adaptor.request_error()
        status = mock.MagicMock()
        status.__str__ = lambda self: 'FAILED_PRECONDITION: in use'
        raise err_cls(status)

    with mock.patch.object(nebius_utils, 'list_security_rules',
                           return_value=[]), \
         mock.patch('sky.adaptors.nebius.sync_call', side_effect=always_fail), \
         mock.patch('sky.adaptors.nebius.sdk'), \
         mock.patch('time.sleep'), \
         mock.patch.object(nebius_utils, 'logger') as mock_logger:
        nebius_utils.delete_security_group('sg-stuck')

    warn_calls = [c.args[0] for c in mock_logger.warning.call_args_list]
    assert any('Failed to delete security group' in m for m in warn_calls)


@nebius_sdk_required
def test_delete_sg_drains_rules_first():
    """Rules must be deleted (and polled to drain) before SG delete."""
    fake_rule = mock.MagicMock()
    fake_rule.metadata.id = 'vpcsecurityrule-abc'

    # list_security_rules: first call returns 1 rule, then [] (drained).
    list_results = iter([[fake_rule], []])

    with mock.patch.object(nebius_utils, 'list_security_rules',
                           side_effect=lambda _sg: next(list_results)), \
         mock.patch.object(nebius_utils, '_delete_security_rule') as mock_del_rule, \
         mock.patch('sky.adaptors.nebius.sync_call'), \
         mock.patch('sky.adaptors.nebius.sdk'), \
         mock.patch('time.sleep'):
        nebius_utils.delete_security_group('sg-with-rules')

    mock_del_rule.assert_called_once_with('vpcsecurityrule-abc')
