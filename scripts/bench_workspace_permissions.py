"""Benchmark: batch workspace permission check vs per-workspace loop.

Measures the latency improvement from get_accessible_workspace_names()
over the old N-call check_workspace_permission() loop.

Usage:
    python scripts/bench_workspace_permissions.py
"""
import os
import tempfile
import time

import casbin
import sqlalchemy_adapter

# Must be set so permission checks run (not short-circuit to True)
os.environ['SKYPILOT_SERVER'] = '1'

from sky.skylet import constants

os.environ[constants.ENV_VAR_IS_SKYPILOT_SERVER] = '1'

MODEL_CONF = os.path.join(os.path.dirname(__file__), '../sky/users/model.conf')
ITERATIONS = 200
WORKSPACE_COUNTS = [10, 50, 100, 200, 300, 400, 500]


def make_enforcer(db_path: str, n_workspaces: int,
                  user_id: str) -> casbin.Enforcer:
    """Create an enforcer with n_workspaces policies for user_id."""
    adapter = sqlalchemy_adapter.Adapter(f'sqlite:///{db_path}')
    enforcer = casbin.Enforcer(MODEL_CONF, adapter)
    for i in range(n_workspaces):
        enforcer.add_policy(user_id, f'workspace-{i}', '*')
    # One public workspace
    enforcer.add_policy('*', 'workspace-public', '*')
    enforcer.save_policy()
    return enforcer


def old_approach(enforcer: casbin.Enforcer, user_id: str,
                 workspace_names: list) -> set:
    """Simulate the old per-workspace check_workspace_permission loop."""
    accessible = set()
    for name in workspace_names:
        if enforcer.enforce(user_id, name, '*'):
            accessible.add(name)
    return accessible


def new_approach(enforcer: casbin.Enforcer, user_id: str,
                 workspace_names: set) -> set:
    """The new batch policy scan (get_accessible_workspace_names)."""
    accessible = set()
    for rule in enforcer.get_policy():
        if len(rule) >= 3 and rule[2] == '*' and (rule[0] == user_id or
                                                  rule[0] == '*'):
            if rule[1] in workspace_names:
                accessible.add(rule[1])
    return accessible


def bench(n_workspaces: int):
    user_id = 'user-abc123'
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        enforcer = make_enforcer(db_path, n_workspaces, user_id)
        workspace_names_list = [f'workspace-{i}' for i in range(n_workspaces)
                               ] + ['workspace-public']
        workspace_names_set = set(workspace_names_list)

        # Warm up
        old_approach(enforcer, user_id, workspace_names_list)
        new_approach(enforcer, user_id, workspace_names_set)

        # Benchmark old
        t0 = time.perf_counter()
        for _ in range(ITERATIONS):
            old_approach(enforcer, user_id, workspace_names_list)
        old_ms = (time.perf_counter() - t0) / ITERATIONS * 1000

        # Benchmark new
        t0 = time.perf_counter()
        for _ in range(ITERATIONS):
            new_approach(enforcer, user_id, workspace_names_set)
        new_ms = (time.perf_counter() - t0) / ITERATIONS * 1000

        speedup = old_ms / new_ms if new_ms > 0 else float('inf')
        print(f'  workspaces={n_workspaces:>4}  '
              f'old={old_ms:.3f}ms  new={new_ms:.3f}ms  '
              f'speedup={speedup:.1f}x')
    finally:
        os.unlink(db_path)


if __name__ == '__main__':
    print(f'Benchmark: {ITERATIONS} iterations per configuration\n')
    print(f'  {"workspaces":>10}  {"old (loop)":>12}  '
          f'{"new (batch)":>12}  {"speedup":>8}')
    print('  ' + '-' * 55)
    for n in WORKSPACE_COUNTS:
        bench(n)
