"""Tests for pinning the Ray version to the cluster, not to the constant.

Deciding per node lets a worker upgrade Ray on its own while the head keeps
the old version -- the head's `ray status` guard succeeds and skips the
install, a worker's cannot -- which leaves the raylet crash-looping against a
mismatched default_worker.py.
"""
import os
import re
from unittest import mock

import pytest

from sky import clouds
from sky import resources as resources_lib
from sky.backends import backend_utils
from sky.skylet import constants
from sky.utils import status_lib

CURRENT = constants.SKY_REMOTE_RAY_VERSION
LEGACY = backend_utils._LEGACY_RAY_VERSION  # pylint: disable=protected-access
KEY = backend_utils._RAY_VERSION_KEY  # pylint: disable=protected-access


def _yaml(recorded=None):
    provider = {'module': 'sky.provision.aws'}
    if recorded is not None:
        provider[KEY] = recorded
    return f'cluster_name: c\nprovider:\n  module: {provider["module"]}\n' + (
        f'  {KEY}: {recorded}\n' if recorded is not None else '')


def _resolve(cluster_name, old_yaml, status):
    with mock.patch.object(backend_utils.global_user_state,
                           'get_status_from_cluster_name',
                           return_value=status):
        return backend_utils._ray_version_for_cluster(  # pylint: disable=protected-access
            cluster_name, old_yaml)


def test_new_cluster_gets_the_current_version():
    assert _resolve('c', None, None) == CURRENT


def test_running_cluster_keeps_its_recorded_version():
    assert _resolve('c', _yaml('2.4.0'), status_lib.ClusterStatus.UP) == '2.4.0'


def test_running_cluster_without_a_marker_is_treated_as_legacy():
    """Clusters created before the marker existed are all on the old Ray."""
    assert _resolve('c', _yaml(), status_lib.ClusterStatus.UP) == LEGACY


def test_a_stopped_cluster_moves_to_the_current_version():
    """Nothing is running to disagree, so the whole cluster can move together.

    This is the only way an existing cluster ever picks up a new Ray.
    """
    stopped = status_lib.ClusterStatus.STOPPED
    assert _resolve('c', _yaml(), stopped) == CURRENT
    assert _resolve('c', _yaml('2.4.0'), stopped) == CURRENT


def test_an_init_cluster_keeps_its_recorded_version():
    """INIT also covers a cluster whose launch failed partway.

    Its nodes may still be running the old Ray, so treating INIT as "safe to
    move" is how the mismatch gets recreated.
    """
    init = status_lib.ClusterStatus.INIT
    assert _resolve('c', _yaml(), init) == LEGACY
    assert _resolve('c', _yaml('2.4.0'), init) == '2.4.0'


def test_marker_lives_under_provider():
    """Ray's cluster schema is additionalProperties=False at the top level.

    A new top-level key would fail `ray up` validation on the clouds still
    using the Ray autoscaler.
    """
    assert KEY not in ('cluster_name', 'provider', 'auth', 'docker')
    # And 'provider' is restored wholesale for existing clusters, which is why
    # write_cluster_config writes the marker back *after* the restore.
    assert 'provider' in (
        backend_utils._RAY_YAML_KEYS_TO_RESTORE_FOR_BACK_COMPATIBILITY)  # pylint: disable=protected-access


# --------------------------------------------------------------- the wiring


def _captured_variables(status, recorded, tmp_path):
    """Run write_cluster_config and return the template variables it built."""
    # Stub the cloud out entirely: what is under test is what
    # write_cluster_config adds on top, and a real AWS call would reach the
    # network.
    with mock.patch('sky.utils.common_utils.fill_template') as fill, \
         mock.patch.object(clouds.AWS,
                           'make_deploy_resources_variables',
                           return_value={}), \
         mock.patch('sky.catalog.instance_type_exists', return_value=True), \
         mock.patch('sky.catalog.get_accelerators_from_instance_type',
                    return_value=None), \
         mock.patch('sky.catalog.get_image_id_from_tag',
                    return_value='fake-image'), \
         mock.patch('sky.catalog.get_arch_from_instance_type',
                    return_value='fake-arch'), \
         mock.patch('sky.check.get_cloud_credential_file_mounts',
                    return_value={}), \
         mock.patch(
             'sky.backends.backend_utils._get_yaml_path_from_cluster_name',
             return_value=str(tmp_path / 'cluster.yml')), \
         mock.patch(
             'sky.backends.backend_utils._deterministic_cluster_yaml_hash',
             return_value='fake-hash'), \
         mock.patch.object(backend_utils.global_user_state,
                           'get_cluster_yaml_str',
                           return_value=(None if status is None else
                                         _yaml(recorded))), \
         mock.patch.object(backend_utils.global_user_state,
                           'get_status_from_cluster_name',
                           return_value=status):
        backend_utils.write_cluster_config(
            to_provision=resources_lib.Resources(cloud=clouds.AWS(),
                                                 instance_type='fake-type'),
            num_nodes=1,
            cluster_config_template='aws-ray.yml.j2',
            cluster_name='c',
            local_wheel_path=tmp_path,
            wheel_hash='fake-wheel-hash',
            region=clouds.Region(name='fake-region'),
            zones=[clouds.Zone(name='fake-zone')],
            dryrun=True,
            keep_launch_fields_in_existing_config=True)
    return fill.call_args[0][1]


def test_a_new_cluster_renders_the_current_version(tmp_path):
    """The version has to reach the install command, not just the marker.

    RAY_INSTALLATION_COMMANDS used to bake SKY_REMOTE_RAY_VERSION in at import
    time, so recording the version per cluster changed nothing: the YAML said
    the old version and still installed the new one.
    """
    variables = _captured_variables(None, None, tmp_path)
    assert variables['ray_version'] == CURRENT
    for key in ('ray_installation_commands',
                'ray_skypilot_installation_commands'):
        assert '{ray_version}' not in variables[key], (
            f'{key} still carries an unsubstituted placeholder')
    assert f'ray[default]=={CURRENT}' in variables['ray_installation_commands']


def test_an_existing_cluster_installs_its_own_ray_but_patches_for_ours(
        tmp_path):
    """The install guard and the patch guard ask different questions.

    Install: is the node on the version *this cluster* wants? Patch: is the
    node's Ray what the patch *files* in this wheel target? On an old cluster
    those differ, and answering both with the cluster's pin applies patches
    generated for the current Ray to the old one -- which corrupts it beyond
    self-repair, since the install guard then skips.
    """
    # A recorded version rather than the no-marker legacy default, so the two
    # stay distinguishable whichever version the constant is on.
    other = '2.4.0'
    assert other != CURRENT
    variables = _captured_variables(status_lib.ClusterStatus.UP, other,
                                    tmp_path)
    assert variables['ray_version'] == other
    assert f'ray[default]=={other}' in variables['ray_installation_commands']
    assert CURRENT in variables['ray_patches_cmd']
    assert other not in variables['ray_patches_cmd'], (
        'the patch step is guarding on the cluster\'s Ray instead of the one '
        'the shipped patch files were generated for')


def test_kubernetes_still_gets_both_commands(tmp_path):
    """Kubernetes installs Ray from the pod args, not from setup_commands.

    Kubernetes.make_deploy_resources_variables no longer supplies these, so if
    write_cluster_config stops doing it the pod renders an empty command and
    silently comes up with whatever Ray the image happened to ship.
    """
    variables = _captured_variables(None, None, tmp_path)
    assert 'ray_installation_commands' in variables
    assert 'apply_patches.py' in variables['ray_patches_cmd']
    assert 'from sky.skylet.ray_patches' not in variables['ray_patches_cmd']


def test_the_marker_does_not_move_the_config_hash(tmp_path):
    """Otherwise every pre-existing cluster loses `--fast` exactly once.

    The marker appears the first time a cluster is launched by a SkyPilot that
    writes one, so its hash would differ from the one stored by the previous
    launch -- a full re-provision for a key that only records what the cluster
    already runs. A real version change still moves the hash, through the
    install commands.
    """
    base = ('cluster_name: c\n'
            'provider:\n'
            '  module: sky.provision.aws\n'
            '  region: us-east-1\n')

    def _hash(text):
        path = tmp_path / f'{abs(hash(text))}.yml'
        path.write_text(text, encoding='utf-8')
        return backend_utils._deterministic_cluster_yaml_hash(str(path))  # pylint: disable=protected-access

    assert _hash(base) == _hash(base + f'  {KEY}: {LEGACY}\n')


def _notice(cloud, pinned='2.4.0', **resource_kwargs):
    """What _log_pinned_ray_version says, split by level."""
    resources = resources_lib.Resources(cloud=cloud,
                                        instance_type='fake-type',
                                        **resource_kwargs)
    with mock.patch.object(backend_utils, 'logger') as log:
        backend_utils._log_pinned_ray_version(  # pylint: disable=protected-access
            'c', pinned, cloud, resources)
    join = lambda calls: ' '.join(str(c) for c in calls)
    return join(log.info.call_args_list), join(log.debug.call_args_list)


def test_a_stoppable_cluster_is_told_to_stop_and_start():
    """A cluster silently staying on an old Ray is unanswerable otherwise."""
    info, _ = _notice(clouds.AWS())
    assert 'sky stop c' in info and 'sky start c' in info


def test_a_cluster_that_cannot_stop_is_not_told_to_stop():
    """Kubernetes, Slurm, RunPod, Lambda and friends have no `sky stop`.

    Naming it would send the user at a command that errors out, and the
    condition is permanent for such a cluster, so it does not belong in every
    launch's output either.
    """
    info, debug = _notice(clouds.Kubernetes())
    assert not info, f'told a Kubernetes user to stop the cluster: {info}'
    assert 'sky stop' not in debug
    assert 'recreated' in debug and 'sky down c' in debug


def test_whether_a_cluster_can_stop_depends_on_the_resources_too():
    """AWS stops on-demand instances but not spot ones."""
    on_demand, _ = _notice(clouds.AWS())
    assert 'sky stop c' in on_demand
    spot_info, spot_debug = _notice(clouds.AWS(), use_spot=True)
    assert not spot_info, f'told a spot user to stop the cluster: {spot_info}'
    assert 'sky down c' in spot_debug


def test_nothing_is_said_when_the_cluster_is_already_current():
    for cloud in (clouds.AWS(), clouds.Kubernetes()):
        info, debug = _notice(cloud, pinned=CURRENT)
        assert not info and not debug, f'{cloud}: {info} {debug}'


# ------------------------------------------------- why stop/start can upgrade


def test_only_kubernetes_installs_ray_where_the_restore_reaches():
    """This is what makes `sky stop` + `sky start` actually move the version.

    An existing cluster has part of its YAML restored from the stored copy, on
    every cloud -- but only the keys in
    _RAY_YAML_KEYS_TO_RESTORE_FOR_BACK_COMPATIBILITY. Kubernetes installs Ray
    from the pod args, inside `node_config`, which is restored, so its clusters
    carry their old Ray forward no matter what is rendered -- and Kubernetes
    cannot `sky stop` either, so nothing moves it. Every other cloud installs
    Ray from top-level `setup_commands`, which is not restored, so a restart
    renders and installs the new version.

    A new cloud template that inlined the install into node_config would
    silently acquire Kubernetes' behaviour, with nothing to say so.
    """
    restored = backend_utils._RAY_YAML_KEYS_TO_RESTORE_FOR_BACK_COMPATIBILITY  # pylint: disable=protected-access
    assert 'node_config' in restored
    assert 'setup_commands' not in restored

    templates_dir = os.path.join(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        'sky', 'templates')
    install = re.compile(r'{{\s*ray_(skypilot_)?installation_commands\s*}}')
    top_level = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):')

    offenders = []
    for name in sorted(os.listdir(templates_dir)):
        if not name.endswith('-ray.yml.j2'):
            continue
        key = None
        with open(os.path.join(templates_dir, name), 'r',
                  encoding='utf-8') as f:
            for lineno, line in enumerate(f, 1):
                matched = top_level.match(line)
                if matched:
                    key = matched.group(1)
                if install.search(line) and key != 'setup_commands':
                    offenders.append(f'{name}:{lineno} under {key!r}')

    assert offenders, 'the scan matched nothing; the templates changed shape'
    unexpected = [
        o for o in offenders if not o.startswith('kubernetes-ray.yml.j2')
    ]
    assert not unexpected, (
        'a template installs Ray under a key that is restored for existing '
        'clusters, so restarting will not move its Ray version: '
        f'{unexpected}')
