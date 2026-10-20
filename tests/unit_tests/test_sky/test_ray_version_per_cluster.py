"""Tests for pinning the Ray version to the cluster, not to the constant.

Deciding per node lets a worker upgrade Ray on its own while the head keeps
the old version -- the head's `ray status` guard succeeds and skips the
install, a worker's cannot -- which leaves the raylet crash-looping against a
mismatched default_worker.py.
"""
import os
import re
import subprocess
from unittest import mock

import pytest

from sky import clouds
from sky import resources as resources_lib
from sky.backends import backend_utils
from sky.skylet import constants
from sky.utils import common
from sky.utils import controller_utils
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


def _notice(cloud, pinned='2.4.0', cluster_name='c', **resource_kwargs):
    """What _log_pinned_ray_version says, split by level."""
    resources = resources_lib.Resources(cloud=cloud,
                                        instance_type='fake-type',
                                        **resource_kwargs)
    with mock.patch.object(backend_utils, 'logger') as log:
        backend_utils._log_pinned_ray_version(  # pylint: disable=protected-access
            cluster_name, pinned, cloud, resources, 'fake-region')
    join = lambda calls: ' '.join(str(c) for c in calls)
    return join(log.info.call_args_list), join(log.debug.call_args_list)


def test_a_stoppable_cluster_is_told_to_stop_and_start():
    """A cluster silently staying on an old Ray is unanswerable otherwise."""
    info, _ = _notice(clouds.AWS())
    assert 'sky stop c' in info and 'sky start c' in info


def test_a_cluster_that_cannot_stop_is_told_to_recreate_it():
    """Kubernetes, Slurm, RunPod, Lambda and friends have no `sky stop`.

    Naming it would send the user at a command that errors out. The cluster
    stays on the old Ray (the one the CVE fix moves away from) until it is
    recreated, so say so where the user sees it.

    Only the cloud I/O is stubbed -- that STOP really is unsupported is the
    cloud's own declaration, asserted just below.
    """
    stop = clouds.CloudImplementationFeatures.STOP
    with mock.patch.object(clouds.Kubernetes,
                           '_unsupported_features_for_resources',
                           return_value={stop: 'no stop on Kubernetes'}):
        info, _ = _notice(clouds.Kubernetes())
    assert 'sky stop' not in info, info
    assert 'recreated' in info and 'sky down c' in info


@pytest.mark.parametrize('controller,idle,unrelated', [
    (controller_utils.Controllers.JOBS_CONTROLLER,
     'no managed jobs in progress and no pools', ['services']),
    (controller_utils.Controllers.SKY_SERVE_CONTROLLER, 'no services',
     ['managed jobs', 'pools']),
])
def test_a_controller_is_not_told_to_stop(controller, idle, unrelated):
    """`sky stop` rejects controllers (core.stop), on every cloud.

    `sky down` refuses a controller that still has work, and what counts as
    work differs between the two.
    """
    name = controller.value.cluster_name
    info, _ = _notice(clouds.AWS(), cluster_name=name)
    assert 'sky stop' not in info, info
    assert f'sky down {name}' in info and 'after an autostop or autodown' in info
    assert f'once it has {idle}.' in info
    assert not [word for word in unrelated if word in info], info


def test_a_controller_under_another_name_is_still_a_controller():
    """A controller launched under another user hash or server id.

    Neither asserted on nor recorded as this process's controller name.
    """
    controller = controller_utils.Controllers.JOBS_CONTROLLER.value
    before = controller.cluster_name
    name = f'{common.JOB_CONTROLLER_PREFIX}{"f" * 8}'
    assert name != before
    info, _ = _notice(clouds.AWS(), cluster_name=name)
    assert f'sky down {name}' in info and 'after an autostop or autodown' in info
    assert controller.cluster_name == before


def test_kubernetes_really_does_declare_stop_unsupported():
    """The premise the test above stubs out, taken from the cloud itself."""
    assert (clouds.CloudImplementationFeatures.STOP
            in clouds.Kubernetes._CLOUD_UNSUPPORTED_FEATURES)  # pylint: disable=protected-access


def test_whether_a_cluster_can_stop_depends_on_the_resources_too():
    """AWS stops on-demand instances but not spot ones."""
    on_demand, _ = _notice(clouds.AWS())
    assert 'sky stop c' in on_demand
    spot_info, _ = _notice(clouds.AWS(), use_spot=True)
    assert 'sky stop' not in spot_info, spot_info
    assert 'sky down c' in spot_info


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


def test_a_bare_provider_key_does_not_raise():
    """`provider:` with nothing under it parses to None, not {}.

    Only a malformed stored YAML gets here, but resolving the version is on
    the launch path -- an AttributeError out of it fails the launch.
    """
    assert _resolve('c', 'cluster_name: c\nprovider:\n',
                    status_lib.ClusterStatus.UP) == LEGACY


def test_the_notice_never_fails_the_launch():
    """It is a log line. Asking the cloud reaches the cloud on some providers.

    Kubernetes answers STOP by enumerating every allowed context and probing
    each cluster's API, catching only KubeAPIUnreachableError -- so a bad
    kubeconfig or an auth-plugin failure would propagate out of a diagnostic
    and abort write_cluster_config.
    """
    cloud = clouds.Kubernetes()
    resources = resources_lib.Resources(cloud=cloud, instance_type='fake-type')
    # Not a mocked failure: a context that is not in the kubeconfig makes
    # Kubernetes raise a bare ValueError out of the feature check.
    with mock.patch.object(backend_utils, 'logger') as log:
        backend_utils._log_pinned_ray_version(  # pylint: disable=protected-access
            'c', '2.4.0', cloud, resources, 'context-that-does-not-exist')
    assert not log.info.call_args_list, 'said something it could not check'
    assert log.debug.call_args_list, 'swallowed it without a word'


def test_the_notice_passes_the_region_it_already_has():
    """Without it, Kubernetes enumerates contexts and probes their APIs.

    None of that work changes the answer: kubernetes.py assigns
    unsupported_features[STOP] unconditionally, before the context loop.
    """
    cloud = clouds.Kubernetes()
    resources = resources_lib.Resources(cloud=cloud, instance_type='fake-type')
    with mock.patch.object(clouds.Kubernetes,
                           'check_features_are_supported') as checked:
        backend_utils._log_pinned_ray_version(  # pylint: disable=protected-access
            'c', '2.4.0', cloud, resources, 'my-context')
    assert checked.call_args.args[2] == 'my-context'


def test_the_ray_install_does_not_upgrade_unrelated_packages():
    """uv's -U re-resolves every dependency to its newest release.

    On an image baked with another Ray that bumped cryptography and urllib3
    past what the image's own Python environment was built against, and code
    that mixes the two (the batch worker) failed to import boto3.
    """
    install = re.search(r'install[^;]*"ray\[default\]==[^;]*',
                        constants.RAY_INSTALLATION_COMMANDS)
    assert install is not None, constants.RAY_INSTALLATION_COMMANDS
    args = install.group(0).split()
    assert not {'-U', '--upgrade', '--reinstall'} & set(args), install.group(0)


def _run_ray_install(tmp_path,
                     install_rc,
                     installed_ray='',
                     ray_cluster_running=False):
    """Runs the Ray install step of RAY_INSTALLATION_COMMANDS with a fake
    `uv pip`: `list` prints `installed_ray`, `install` exits `install_rc` and
    is counted in tmp_path/installs. Only that step: the commands before it
    install uv from the network."""
    fake_uv_pip = tmp_path / 'uv-pip'
    fake_uv_pip.write_text(
        '#!/bin/bash\n'
        f'if [ "$1" = install ]; then echo x >> {tmp_path}/installs; '
        f'exit {install_rc}; fi\n'
        f'[ "$1" = list ] && echo "{installed_ray}"\n'
        'exit 0\n')
    fake_uv_pip.chmod(0o755)
    full = constants.RAY_INSTALLATION_COMMANDS
    start = full.index(f'{constants.SKY_UV_PIP_CMD} list | grep "ray "')
    end = full.index('exit 1; };', start) + len('exit 1; };')
    cmd = (full[start:end].replace('{ray_version}', '9.9.9').replace(
        constants.SKY_UV_PIP_CMD, str(fake_uv_pip)).replace(
            constants.RAY_STATUS,
            'true' if ray_cluster_running else 'false').replace(
                constants.SKY_PYTHON_CMD, 'python3'))
    return subprocess.run(['bash', '-c', cmd],
                          env={
                              'HOME': str(tmp_path),
                              'PATH': os.environ['PATH']
                          },
                          capture_output=True,
                          text=True,
                          timeout=60,
                          check=False)


def _installs(tmp_path):
    path = tmp_path / 'installs'
    return len(path.read_text().splitlines()) if path.exists() else 0


def test_a_failed_ray_install_fails_the_setup_loudly(tmp_path):
    """Ray 2.52+ needs Python 3.10, and the commands are joined with `;`.

    A runtime env from before it was pinned to 3.10 cannot install the new
    Ray. Carrying on silently left the node on the old Ray with the patches
    skipped.
    """
    proc = _run_ray_install(tmp_path, install_rc=1)
    assert proc.returncode == 1, proc.stderr
    assert 'could not install Ray 9.9.9' in proc.stderr
    assert 'Python 3.' in proc.stderr


def test_a_successful_ray_install_does_not_report_a_failure(tmp_path):
    proc = _run_ray_install(tmp_path, install_rc=0)
    assert proc.returncode == 0, proc.stderr
    assert 'could not install Ray' not in proc.stderr
    assert _installs(tmp_path) == 1


def test_a_node_already_on_the_version_does_not_install(tmp_path):
    proc = _run_ray_install(tmp_path,
                            install_rc=1,
                            installed_ray='ray        9.9.9')
    assert proc.returncode == 0, proc.stderr
    assert _installs(tmp_path) == 0


def test_a_running_ray_cluster_on_another_version_is_left_alone(tmp_path):
    """An existing cluster keeps its Ray: the install must not run, let alone
    fail the setup, while a Ray cluster answers on the port."""
    proc = _run_ray_install(tmp_path,
                            install_rc=1,
                            installed_ray='ray        2.4.0',
                            ray_cluster_running=True)
    assert proc.returncode == 0, proc.stderr
    assert _installs(tmp_path) == 0
