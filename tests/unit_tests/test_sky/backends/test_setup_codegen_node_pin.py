"""Contract tests for Ray setup scheduling (node:<ip> pin, no CPU ask).

Setup must place on every alive node — including dedicated thin heads that
advertise ``num-cpus=0``. ``ray start --num-cpus`` is integer-only, so a
fractional CPU token is not viable; setup therefore pins via the per-node
``node:<ip>`` resource with ``num_cpus=0``.

These tests lock that contract for homogeneous CPU, multi-node, and
CPU+GPU (multi-resource) codegen so thin-head work cannot regress ordinary
jobs.
"""

from sky.backends import task_codegen


def _generate_setup_code(*, num_nodes: int, resources_dict: dict,
                         setup_cmd: str = 'echo setup') -> str:
    """Return RayCodeGen output for a job with setup.

    Args:
        num_nodes: Node count passed to add_setup / add_task.
        resources_dict: Ray resources for the job placement group / task
            (e.g. ``{'CPU': 2.0}`` or ``{'CPU': 2.0, 'GPU': 1.0}``).
        setup_cmd: Bash setup command embedded in the generated script.

    Returns:
        Full generated driver script from ``RayCodeGen.build()``.
    """
    codegen = task_codegen.RayCodeGen()
    codegen.add_prologue(job_id=99)
    ips = [f'10.0.0.{i + 1}' for i in range(num_nodes)]
    env = {'SKYPILOT_TASK_ID': 'sky-test-setup-pin'}
    codegen.add_setup(
        num_nodes,
        resources_dict=dict(resources_dict),
        stable_cluster_internal_ips=ips,
        env_vars=env,
        log_dir='/sky/logs',
        setup_cmd=setup_cmd,
    )
    codegen.add_task(
        num_nodes,
        bash_script='echo run',
        task_name='run_task',
        resources_dict=dict(resources_dict),
        log_dir='/sky/logs/tasks',
        env_vars=env,
    )
    codegen.add_epilogue()
    return codegen.build()


def _assert_setup_pins_by_node_ip(code: str) -> None:
    """Assert setup placement uses node:<ip> bundles and num_cpus=0."""
    assert 'NodeManagerAddress' in code
    assert 'setup_bundles' in code
    assert 'num_cpus=0' in code
    # Old token-CPU path must not return.
    assert '_SETUP_CPUS' not in code
    assert '"CPU": _SETUP_CPUS' not in code
    assert "'CPU': _SETUP_CPUS" not in code
    # Bundle construction must key on node:<ip>, not CPU.
    assert 'node:{n' in code or "node:{n['NodeManagerAddress']}" in code


def test_setup_codegen_homogeneous_single_node_pins_by_node_ip():
    code = _generate_setup_code(num_nodes=1, resources_dict={'CPU': 4.0})
    _assert_setup_pins_by_node_ip(code)


def test_setup_codegen_homogeneous_multi_node_pins_by_node_ip():
    """Regression: ordinary multi-node (no thin head) still gets setup."""
    code = _generate_setup_code(num_nodes=3, resources_dict={'CPU': 2.0})
    _assert_setup_pins_by_node_ip(code)
    # Job PG still spreads CPU across num_nodes (unchanged run path).
    assert code.count('STRICT_SPREAD') >= 2  # setup_pg + job pg


def test_setup_codegen_multi_resource_cpu_gpu_pins_by_node_ip():
    """CPU+GPU nodes: setup must not require CPU; run still asks for GPU.

    \"Multi-resource\" here means Ray nodes that advertise more than CPU
    (e.g. CPU and GPU). Setup used to reserve a tiny CPU on every node;
    with thin heads that breaks. Setup must pin by node IP only, while the
    task placement group may still request CPU+GPU on worker-shaped nodes.
    """
    code = _generate_setup_code(
        num_nodes=2,
        resources_dict={
            'CPU': 4.0,
            'GPU': 1.0
        },
    )
    _assert_setup_pins_by_node_ip(code)
    # Run / job path still encodes GPU demand for the actual task.
    assert 'GPU' in code
    assert 'num_gpus=1.0' in code or '"GPU": 1.0' in code
