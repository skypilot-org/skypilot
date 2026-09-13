"""Unit tests for the per-context behavior of sky.usage.usage_lib.messages."""
import asyncio
import contextvars
import json
import time
import types

import pytest

from sky import clouds as sky_clouds
from sky.usage import usage_lib
from sky.utils import context
from sky.utils import env_options


def _reset_module_state():
    """Reset per-context state between tests.

    Tests share the test runner's contextvars Context, so a value set by
    one test would otherwise leak into the next. Clearing
    ``_messages_var`` makes each test start with an empty Context.
    """
    usage_lib._messages_var.set(None)


def test_proxy_returns_same_instance_within_one_context():
    """Within one contextvars Context, lazy creation is idempotent."""
    _reset_module_state()

    a = usage_lib._get_messages()
    b = usage_lib._get_messages()
    assert a is b
    # Mutations are visible across reads of the same instance.
    a.usage.run_id = 'mutated'
    assert b.usage.run_id == 'mutated'


def test_proxy_attribute_access_round_trips():
    """messages.usage / messages.heartbeat go through the proxy."""
    _reset_module_state()

    # Read a few fields to make sure attribute proxying works.
    assert usage_lib.messages.usage.run_id is not None
    assert usage_lib.messages.heartbeat.interval_seconds > 0


@pytest.mark.asyncio
async def test_concurrent_contextual_async_get_isolated_messages():
    """Each @contextual_async coroutine has its own MessageCollection.

    Regression for the consolidation-mode bug: the consolidated managed
    jobs controller process serves many JobController coroutines, and a
    process-wide MessageCollection would let one job's mutations leak
    into another job's telemetry payload. With the proxy, each
    @contextual_async invocation runs inside its own copied
    contextvars Context and so triggers an independent lazy creation of
    its MessageCollection.
    """
    _reset_module_state()

    seen_ids = []

    @context.contextual_async
    async def write_and_read(tag: str, delay: float) -> str:
        # First access triggers lazy creation in this coroutine's
        # copied contextvars Context.
        usage_lib.messages.usage.run_id = f'run-{tag}'
        seen_ids.append(id(usage_lib._get_messages()))
        # Yield so the sibling coroutine has a chance to run between
        # our write and our read.
        await asyncio.sleep(delay)
        return usage_lib.messages.usage.run_id

    a, b = await asyncio.gather(
        write_and_read('a', delay=0.05),
        write_and_read('b', delay=0.0),
    )

    # Each coroutine sees its own write — no cross-contamination.
    assert a == 'run-a', a
    assert b == 'run-b', b
    # And the underlying MessageCollections are distinct objects.
    assert len(set(seen_ids)) == 2, seen_ids


@pytest.mark.asyncio
async def test_reset_isolates_to_current_context():
    """messages.reset(USAGE) only resets the current context's collection."""
    _reset_module_state()

    sibling_run_ids = []

    @context.contextual_async
    async def sibling_observe(delay: float) -> str:
        usage_lib.messages.usage.run_id = 'sibling-run'
        await asyncio.sleep(delay)
        return usage_lib.messages.usage.run_id

    @context.contextual_async
    async def reset_and_observe(delay: float) -> str:
        usage_lib.messages.usage.run_id = 'reset-run'
        await asyncio.sleep(delay)
        usage_lib.messages.reset(usage_lib.MessageType.USAGE)
        # After reset the current context has a fresh UsageMessageToReport;
        # its run_id should NOT be 'reset-run' anymore.
        return usage_lib.messages.usage.run_id

    sibling_id, post_reset_id = await asyncio.gather(
        sibling_observe(delay=0.05),
        reset_and_observe(delay=0.0),
    )

    # Sibling kept its run id across the other coroutine's reset.
    assert sibling_id == 'sibling-run'
    # The reset coroutine sees a fresh UsageMessageToReport, not its
    # earlier 'reset-run'.
    assert post_reset_id != 'reset-run'


def test_messages_proxy_supports_collection_protocol():
    """The proxy exposes __getitem__ / items / values like the original."""
    _reset_module_state()

    by_index = usage_lib.messages[usage_lib.MessageType.USAGE]
    by_attr = usage_lib.messages.usage
    assert by_index is by_attr

    keys = {k for k, _ in usage_lib.messages.items()}
    assert usage_lib.MessageType.USAGE in keys
    assert usage_lib.MessageType.HEARTBEAT in keys
    assert usage_lib.MessageType.SERVER_HEARTBEAT in keys

    values = list(usage_lib.messages.values())
    assert len(values) == 3


def test_install_fresh_messages_overrides_inherited_collection(monkeypatch):
    """install_fresh_messages_for_current_context replaces an inherited MC.

    Regression: in consolidation mode, class-body decorators like
    ``@usage_lib.messages.usage.update_runtime('provision')`` trigger
    lazy MC creation at module import time, against the controller
    process's startup env. Subsequent JobController coroutines
    inherit that MC via ContextVar copy and would otherwise share it.
    install_fresh_messages_for_current_context lets a per-task entry
    point install its own MC after establishing a per-task env.
    """
    _reset_module_state()

    # Simulate a "parent" creating an MC against env=startup-run.
    monkeypatch.setenv(usage_lib.constants.USAGE_RUN_ID_ENV_VAR, 'startup-run')
    inherited = usage_lib._get_messages()
    assert inherited.usage.run_id == 'startup-run'

    # Now flip the env (mimicking dotenv into ctx.override_envs from a
    # per-job env file) and install a fresh MC.
    monkeypatch.setenv(usage_lib.constants.USAGE_RUN_ID_ENV_VAR, 'per-task-run')
    usage_lib.install_fresh_messages_for_current_context()

    refreshed = usage_lib._get_messages()
    assert refreshed is not inherited
    assert refreshed.usage.run_id == 'per-task-run'


@pytest.mark.asyncio
async def test_install_fresh_messages_isolates_concurrent_tasks(monkeypatch):
    """When the parent Context already has an MC, two
    @contextual_async children that each call install_fresh_messages
    do not see each other's state — even though they would otherwise
    inherit the parent's MC."""
    _reset_module_state()

    # Create a parent MC so children would otherwise inherit it.
    monkeypatch.setenv(usage_lib.constants.USAGE_RUN_ID_ENV_VAR, 'parent-run')
    _ = usage_lib._get_messages()

    @context.contextual_async
    async def per_task(run_id: str, delay: float) -> str:
        # Set the env first (mimicking the env-file load), then install
        # a fresh MC, then read back through the proxy.
        monkeypatch.setenv(usage_lib.constants.USAGE_RUN_ID_ENV_VAR, run_id)
        usage_lib.install_fresh_messages_for_current_context()
        await asyncio.sleep(delay)
        return usage_lib.messages.usage.run_id

    a, b = await asyncio.gather(
        per_task('task-a', delay=0.05),
        per_task('task-b', delay=0.0),
    )

    assert a == 'task-a', a
    assert b == 'task-b', b


def _enable_usage_collection(monkeypatch):
    """Undo the suite-wide SKYPILOT_DISABLE_USAGE_COLLECTION=1.

    pyproject.toml sets it for every test, and the capacity recorders honour
    it by recording nothing — so these tests have to opt back in.
    """
    monkeypatch.delenv('SKYPILOT_DISABLE_USAGE_COLLECTION', raising=False)


class _FakeKvCache:
    """Dict-backed stand-in for sky.utils.db.kv_cache honouring expires_at."""

    def __init__(self):
        self.rows = {}
        self.writes = []

    def get_cache_entry(self, key):
        row = self.rows.get(key)
        if row is None or row[1] <= time.time():
            return None
        return row[0]

    def add_or_update_cache_entry(self, key, value, expires_at):
        self.writes.append(key)
        self.rows[key] = (value, expires_at)

    def get_cache_entries_by_prefix(self, prefix):
        return {
            k: v
            for k, (v, exp) in self.rows.items()
            if k.startswith(prefix) and exp > time.time()
        }

    def expire(self, key):
        value, _ = self.rows[key]
        self.rows[key] = (value, time.time() - 1)


@pytest.fixture
def capacity_store(monkeypatch):
    """A heartbeat process with an empty capacity cache and telemetry on."""
    _reset_module_state()
    _enable_usage_collection(monkeypatch)
    fake = _FakeKvCache()
    monkeypatch.setattr('sky.utils.db.kv_cache.get_cache_entry',
                        fake.get_cache_entry)
    monkeypatch.setattr('sky.utils.db.kv_cache.add_or_update_cache_entry',
                        fake.add_or_update_cache_entry)
    monkeypatch.setattr('sky.utils.db.kv_cache.get_cache_entries_by_prefix',
                        fake.get_cache_entries_by_prefix)
    return fake


def _fake_node(acc_type, count):
    return types.SimpleNamespace(accelerator_type=acc_type,
                                 total={'accelerator_count': count})


def _fake_nodes_info(*nodes):
    return types.SimpleNamespace(node_info_dict={
        f'n{i}': n for i, n in enumerate(nodes)
    })


def _fake_slurm_node(node, gres, partition='batch'):
    return types.SimpleNamespace(node=node, gres=gres, partition=partition)


def _k8s_key(context):
    return (usage_lib.constants.NODE_INFO_CACHE_KEY_PREFIX +
            usage_lib._k8s_capacity_id(context))


def _slurm_key(cluster):
    return (usage_lib.constants.NODE_INFO_CACHE_KEY_PREFIX +
            usage_lib._slurm_capacity_id(cluster))


def test_collect_gpu_fleet(monkeypatch, capacity_store):
    """Capacity is summed over Kubernetes, SSH and Slurm node inventory."""
    per_context = {
        'ctx-a': _fake_nodes_info(_fake_node('H100', 8), _fake_node('H100', 8),
                                  _fake_node(None, 0)),
        # Unreachable context — drops out, does not fail the heartbeat.
        'ctx-b': None,
        'ssh-pool': _fake_nodes_info(_fake_node('A100:80GB', 4),
                                     _fake_node('tpu-v5e-8', 8)),
    }

    def fake_node_info(context=None):
        info = per_context[context]
        if info is None:
            raise RuntimeError('context unreachable')
        return info

    monkeypatch.setattr(
        'sky.provision.kubernetes.utils._get_kubernetes_node_info',
        fake_node_info)
    monkeypatch.setattr(
        'sky.provision.slurm.utils.get_slurm_nodes_info', lambda cluster: [
            _fake_slurm_node('s1', 'gpu:h100:4'),
            _fake_slurm_node('s2', 'gpu:2'),
        ])

    counts = usage_lib._collect_gpu_fleet(['ctx-a', 'ctx-b', 'ssh-pool'],
                                          ['slurm-a'])
    assert counts == {'H100': 16, 'A100:80GB': 4, 'h100': 4, 'gpu': 2}

    # Every reachable infra was recorded for the next tick.
    assert _k8s_key('ctx-a') in capacity_store.rows
    assert _k8s_key('ssh-pool') in capacity_store.rows
    assert _slurm_key('slurm-a') in capacity_store.rows
    assert _k8s_key('ctx-b') not in capacity_store.rows


def test_slurm_nodes_are_deduped_across_partitions():
    """sinfo --Node repeats a node per partition; count it once."""
    counts = usage_lib._gpu_capacity_from_slurm_nodes([
        _fake_slurm_node('s1', 'gpu:h100:8', partition='batch'),
        _fake_slurm_node('s1', 'gpu:h100:8', partition='debug'),
        _fake_slurm_node('s2', '(null)'),
    ])
    assert counts == {'h100': 8}


def test_recorded_rows_skip_the_fetch(monkeypatch, capacity_store):
    """Fresh rows are read in one query and their infras are not fetched."""
    fetched = []

    def fake_node_info(context=None):
        fetched.append(context)
        return _fake_nodes_info(_fake_node('H100', 8))

    monkeypatch.setattr(
        'sky.provision.kubernetes.utils._get_kubernetes_node_info',
        fake_node_info)

    # Something else — a dashboard request, say — already recorded ctx-a.
    usage_lib.record_node_info('ctx-a', _fake_nodes_info(_fake_node('H100', 8)))

    counts = usage_lib._collect_gpu_fleet(['ctx-a', 'ctx-b'], [])
    assert counts == {'H100': 16}
    assert fetched == ['ctx-b']

    # Once the row expires the context is fetched again.
    capacity_store.expire(_k8s_key('ctx-a'))
    usage_lib._collect_gpu_fleet(['ctx-a'], [])
    assert fetched == ['ctx-b', 'ctx-a']


def test_record_gpu_capacity_leaves_fresh_rows_alone(capacity_store):
    """A busy node-info reader must not upsert on every call."""
    counts = {'H100': 8}
    for _ in range(20):
        usage_lib.record_gpu_capacity('kubernetes/ctx', counts)
    assert len(capacity_store.writes) == 1

    capacity_store.expire(_k8s_key('ctx'))
    usage_lib.record_gpu_capacity('kubernetes/ctx', counts)
    assert len(capacity_store.writes) == 2

    # Only the derived counts are stored, never the inventory.
    value, _ = capacity_store.rows[_k8s_key('ctx')]
    assert json.loads(value) == counts


def test_node_info_not_recorded_when_usage_collection_disabled(
        monkeypatch, capacity_store):
    """An opted-out server accumulates no capacity state."""
    monkeypatch.setenv('SKYPILOT_DISABLE_USAGE_COLLECTION', '1')
    usage_lib.record_node_info('ctx', _fake_nodes_info(_fake_node('H100', 8)))
    assert not capacity_store.rows


def test_enforced_usage_collection_overrides_the_env_var(monkeypatch):
    """A deployment that pins usage collection on must keep recording.

    An enterprise plugin enforces collection by patching
    ``env_options.Options.get`` so ``DISABLE_LOGGING`` reads False whatever
    the environment says. Every opt-out check on the heartbeat path must go
    through that accessor, never the environment directly, or the plugin's
    override silently stops applying to the new fields.
    """
    _reset_module_state()
    fake = _FakeKvCache()
    monkeypatch.setattr('sky.utils.db.kv_cache.get_cache_entry',
                        fake.get_cache_entry)
    monkeypatch.setattr('sky.utils.db.kv_cache.add_or_update_cache_entry',
                        fake.add_or_update_cache_entry)

    # The user opted out...
    monkeypatch.setenv('SKYPILOT_DISABLE_USAGE_COLLECTION', '1')
    # ...but the deployment pins collection on, the way the plugin does.
    original_get = env_options.Options.get

    def _enforced_get(self):
        if self == env_options.Options.DISABLE_LOGGING:
            return False
        return original_get(self)

    monkeypatch.setattr(env_options.Options, 'get', _enforced_get)

    usage_lib.record_node_info('ctx', _fake_nodes_info(_fake_node('H100', 8)))
    assert _k8s_key('ctx') in fake.rows, 'enforcement must reach recording'

    # And the message itself is still sent.
    sent = []
    monkeypatch.setattr(
        usage_lib.requests, 'post',
        lambda *a, **k: sent.append(k) or types.SimpleNamespace(status_code=204,
                                                                text=''))
    usage_lib._send_to_loki(usage_lib.MessageType.SERVER_HEARTBEAT)
    assert len(sent) == 1, 'enforcement must reach the Loki send'


def test_collect_infra_count(monkeypatch):
    """Each infra kind is counted; 'clouds' excludes the separate keys."""
    monkeypatch.setattr('sky.skypilot_config.get_nested',
                        lambda keys, default_value=None, **kw: {'team-a': {}}
                        if keys == ('workspaces',) else default_value)
    # 'kubernetes' and 'slurm' are already counted under their own keys, so
    # they must not also land in the 'clouds' total.
    monkeypatch.setattr(
        'sky.global_user_state.get_cached_enabled_clouds',
        lambda capability, workspace: [
            sky_clouds.AWS(),
            sky_clouds.GCP(),
            sky_clouds.Kubernetes(),
            sky_clouds.Slurm(),
        ])

    infra_count = usage_lib._collect_infra_count(['ctx-a', 'ctx-b', 'ctx-c'],
                                                 ['ssh-pool'], [])
    assert infra_count == {
        'kubernetes': 3,
        'ssh_node_pools': 1,
        'slurm': 0,
        'clouds': 2,
    }


def test_collect_infra_count_omits_failed_lookups(monkeypatch):
    """A failed lookup (None) drops its key; an empty one reports zero."""
    monkeypatch.setattr('sky.skypilot_config.get_nested',
                        lambda keys, default_value=None, **kw: default_value)
    monkeypatch.setattr('sky.global_user_state.get_cached_enabled_clouds',
                        lambda capability, workspace: [sky_clouds.AWS()])

    infra_count = usage_lib._collect_infra_count(None, ['ssh-pool'],
                                                 ['slurm-a'])
    assert 'kubernetes' not in infra_count
    assert infra_count == {'ssh_node_pools': 1, 'slurm': 1, 'clouds': 1}


def test_plugins_field_is_provider_gated():
    """'plugins' appears only with a provider; the counts always send."""
    _reset_module_state()
    original = dict(usage_lib.ServerHeartbeatMessage._data_providers)
    usage_lib.ServerHeartbeatMessage._data_providers.clear()
    try:
        properties = usage_lib.messages.server_heartbeat.get_properties()
        assert 'plugins' not in properties
        # The GPU and infra fields are present regardless.
        assert properties['total_gpus'] == 0
        assert properties['gpus_by_type'] == {}
        assert properties['infra_count'] == {}

        usage_lib.ServerHeartbeatMessage.register_provider(
            'billing', lambda: {'gpu_inventory': 8})
        properties = usage_lib.messages.server_heartbeat.get_properties()
        assert properties['plugins'] == {'billing': {'gpu_inventory': 8}}
    finally:
        usage_lib.ServerHeartbeatMessage._data_providers.clear()
        usage_lib.ServerHeartbeatMessage._data_providers.update(original)
