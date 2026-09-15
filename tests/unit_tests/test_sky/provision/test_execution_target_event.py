"""The 'Instances launched' event names where the instances actually ran.

The event has always named the region the cluster was submitted to. For a
provider that submits to one control plane and executes on another, that
names the control plane and implies the work runs there, so
EXECUTION_TARGET_RESOLVERS lets the provider supply the other half.

Two properties carry the risk. Reporting must never change what provisioning
*does* -- a resolver that raises, or that answers with the submission target
itself, has to leave the event exactly as it was before any hook existed. And
the hook must stay inert for the providers that register none, which is every
provider in tree.
"""
import contextlib
from unittest import mock

import pytest

from sky import clouds
from sky import global_user_state
from sky.provision import common as provision_common
from sky.provision import provisioner
from sky.utils import resources_utils

_REGION = 'my-region'
_OTHER = 'my-other-cluster'


@pytest.fixture(name='resolvers')
def resolvers_fixture(monkeypatch):
    """An empty resolver registry, restored after the test.

    The registry is module-level state, so a test that appended to the real
    list would leak into every test that ran after it.
    """
    registry = []
    monkeypatch.setattr(provisioner, 'EXECUTION_TARGET_RESOLVERS', registry)
    return registry


@pytest.fixture(name='launched_message')
def launched_message_fixture(monkeypatch):
    """Provision once with the cloud and state deps stubbed out.

    Returns a callable handing back the 'Instances launched' event text.
    Kubernetes is deliberate: `_bulk_provision` skips its wait/retry loop for
    the Kubernetes-based clouds, so nothing here has to stub that out.
    """
    monkeypatch.setattr(provisioner.provision_volume,
                        'provision_ephemeral_volumes', lambda *a, **k: None)
    monkeypatch.setattr(provisioner.provision, 'bootstrap_instances',
                        lambda *a, **k: mock.MagicMock())
    monkeypatch.setattr(
        provisioner.provision, 'run_instances', lambda *a, **k: provision_common
        .ProvisionRecord(provider_name='kubernetes',
                         region=_REGION,
                         zone=None,
                         cluster_name='c-on-cloud',
                         head_instance_id='head',
                         resumed_instance_ids=[],
                         created_instance_ids=['head']))
    monkeypatch.setattr(provisioner.provision_logging,
                        'setup_provision_logging',
                        lambda *a, **k: contextlib.nullcontext())

    events = []
    monkeypatch.setattr(
        global_user_state, 'add_cluster_event',
        lambda name, status, reason, event_type: events.append(reason))

    def _provision():
        provisioner._bulk_provision(  # pylint: disable=protected-access
            cloud=clouds.Kubernetes(),
            region=clouds.Region(_REGION),
            cluster_name=resources_utils.ClusterName('c', 'c-on-cloud'),
            bootstrap_config=provision_common.ProvisionConfig(
                provider_config={},
                authentication_config={},
                docker_config={},
                node_config={},
                count=1,
                tags={},
                resume_stopped_nodes=True,
                ports_to_open_on_launch=None))
        launched = [e for e in events if e.startswith('Instances launched')]
        assert len(launched) == 1, events
        return launched[0]

    return _provision


class TestResolution:
    """`_resolve_execution_target` in isolation."""

    def test_no_resolvers_means_no_answer(self, resolvers):
        """The in-tree default: nothing registers a hook."""
        del resolvers
        assert provisioner._resolve_execution_target(  # pylint: disable=protected-access
            'kubernetes', _REGION, 'c-on-cloud') is None

    def test_the_first_resolver_with_an_answer_wins(self, resolvers):
        resolvers.extend([
            lambda *a: None,
            lambda *a: _OTHER,
            lambda *a: 'never-reached',
        ])
        assert provisioner._resolve_execution_target(  # pylint: disable=protected-access
            'kubernetes', _REGION, 'c-on-cloud') == _OTHER

    def test_a_target_equal_to_the_region_is_suppressed(self, resolvers):
        """Nothing to report: the instances ran where they were submitted."""
        resolvers.append(lambda *a: _REGION)
        assert provisioner._resolve_execution_target(  # pylint: disable=protected-access
            'kubernetes', _REGION, 'c-on-cloud') is None

    def test_a_raising_resolver_does_not_stop_the_others(self, resolvers):

        def _boom(*args):
            raise RuntimeError('resolver is broken')

        resolvers.extend([_boom, lambda *a: _OTHER])
        assert provisioner._resolve_execution_target(  # pylint: disable=protected-access
            'kubernetes', _REGION, 'c-on-cloud') == _OTHER

    def test_a_raising_resolver_alone_yields_no_answer(self, resolvers):

        def _boom(*args):
            raise RuntimeError('resolver is broken')

        resolvers.append(_boom)
        assert provisioner._resolve_execution_target(  # pylint: disable=protected-access
            'kubernetes', _REGION, 'c-on-cloud') is None

    def test_resolvers_see_the_provider_region_and_cluster(self, resolvers):
        """Providers branch on these, so pin what they are handed."""
        seen = []
        resolvers.append(lambda *args: seen.append(args))
        provisioner._resolve_execution_target(  # pylint: disable=protected-access
            'kubernetes', _REGION, 'c-on-cloud')
        assert seen == [('kubernetes', _REGION, 'c-on-cloud')]

    def test_a_resolver_raising_a_bare_exit_is_contained(self, resolvers):
        """SystemExit and KeyboardInterrupt derive from BaseException, and
        escaping here would be read as user cancellation by bulk_provision --
        filing an already-launched cluster as cancelled."""

        def _exit(*args):
            raise SystemExit(1)

        resolvers.append(_exit)
        assert provisioner._resolve_execution_target(  # pylint: disable=protected-access
            'kubernetes', _REGION, 'c-on-cloud') is None


class TestRegistration:
    """`register_execution_target_resolver` is the supported entry point."""

    def test_registering_adds_the_resolver(self, resolvers):

        def _resolver(*args):
            del args

        provisioner.register_execution_target_resolver(_resolver)
        assert resolvers == [_resolver]

    def test_registering_twice_keeps_one(self, resolvers):
        """A module registering on import is loaded in several process
        contexts; the list must not grow on each."""

        def _resolver(*args):
            del args

        provisioner.register_execution_target_resolver(_resolver)
        provisioner.register_execution_target_resolver(_resolver)
        assert resolvers == [_resolver]


class TestEvent:
    """The text the event actually carries."""

    def test_without_a_resolver_only_the_region_is_named(
            self, resolvers, launched_message):
        del resolvers
        assert launched_message() == ('Instances launched on kubernetes in '
                                      f'{_REGION}')

    def test_the_region_is_not_the_dataclass_repr(self, resolvers,
                                                  launched_message):
        """Regression: the event used to interpolate the Region object."""
        del resolvers
        assert 'Region(' not in launched_message()

    def test_a_differing_execution_target_is_named(self, resolvers,
                                                   launched_message):
        resolvers.append(lambda *a: _OTHER)
        assert launched_message() == ('Instances launched on kubernetes in '
                                      f'{_REGION}, running on {_OTHER}')

    def test_a_canonically_named_provider_hook_fires(self, resolvers,
                                                     launched_message):
        """Regression: the provider name reaching hooks used to be
        ``repr(cloud)`` ('Kubernetes'), so a hook matching the canonical name
        the provision registry dispatches on never fired and the placement was
        silently dropped from the event."""

        def _only_kubernetes(provider_name, region_name, cluster_name_on_cloud):
            del region_name, cluster_name_on_cloud
            return _OTHER if provider_name == 'kubernetes' else None

        resolvers.append(_only_kubernetes)
        assert launched_message() == ('Instances launched on kubernetes in '
                                      f'{_REGION}, running on {_OTHER}')

    def test_hooks_are_handed_the_canonical_provider_name(
            self, resolvers, launched_message):
        """The contract, pinned at the real call site rather than asserted of
        a hand-built argument."""
        seen = []
        resolvers.append(lambda *args: seen.append(args[0]))
        launched_message()
        assert seen == ['kubernetes']

    def test_a_resolver_answering_the_region_changes_nothing(
            self, resolvers, launched_message):
        resolvers.append(lambda *a: _REGION)
        assert launched_message() == ('Instances launched on kubernetes in '
                                      f'{_REGION}')

    def test_a_raising_resolver_changes_nothing(self, resolvers,
                                                launched_message):
        """Reporting must never be what fails a launch."""

        def _boom(*args):
            raise RuntimeError('resolver is broken')

        resolvers.append(_boom)
        assert launched_message() == ('Instances launched on kubernetes in '
                                      f'{_REGION}')
