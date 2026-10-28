"""Cross-process regression test for ssh key bootstrap convergence.

Models processes that share one database but have separate local key
directories (separate $HOME): both call get_or_generate_keys()
simultaneously for a brand-new user. The loser of the insert race must
adopt the winner's key pair, so both local pairs and the database row
converge on a single value.
"""
import os
import subprocess
import sys
import textwrap
import time

from sky.skylet import constants

_USER_HASH = '9f8e7d6c'

# Both processes import sky before the barrier so import time does not
# widen the race window artificially, then race on a file-based barrier.
_REPLICA_SCRIPT = textwrap.dedent("""
    import os
    import sys
    import time

    base = os.environ['RACE_BASE']
    pod = os.environ['RACE_POD']

    from sky.utils import auth_utils

    ready_file = os.path.join(base, f'ready-{pod}')
    sibling_file = os.path.join(base, 'ready-'
                                + ('b' if pod == 'a' else 'a'))
    go_file = os.path.join(base, 'go')
    with open(ready_file, 'w') as f:
        f.write('ready')

    # The go-wait deadline must not start until the sibling is imported
    # and ready too, so a slow sibling import under CPU contention cannot
    # masquerade as a test failure.
    deadline = None
    while True:
        if deadline is None and os.path.exists(sibling_file):
            deadline = time.time() + 60
        if os.path.exists(go_file):
            break
        if deadline is not None and time.time() > deadline:
            sys.exit('timed out waiting for go signal')
        time.sleep(0.001)

    auth_utils.get_or_generate_keys()
    """)


def _run_replicas(base: str) -> None:
    """Spawn two racing processes sharing a DB with separate $HOME."""
    proc_a = os.path.join(base, 'proc-a')
    proc_b = os.path.join(base, 'proc-b')
    shared = os.path.join(base, 'shared')
    for d in (proc_a, proc_b, shared):
        os.makedirs(d)

    # Pre-create the DB schema, as a running deployment would have.
    env_init = dict(os.environ)
    env_init['HOME'] = proc_a
    env_init[constants.SKY_RUNTIME_DIR_ENV_VAR_KEY] = shared
    env_init[constants.USER_ID_ENV_VAR] = _USER_HASH
    subprocess.run(
        [
            sys.executable, '-c', 'from sky import global_user_state; '
            f'assert not global_user_state.get_ssh_keys({_USER_HASH!r})[2]'
        ],
        env=env_init,
        check=True,
        capture_output=True,
        timeout=120,
    )

    script_path = os.path.join(base, 'replica.py')
    with open(script_path, 'w') as f:
        f.write(_REPLICA_SCRIPT)

    procs = {}
    for pod in ('a', 'b'):
        env = dict(os.environ)
        env['HOME'] = os.path.join(base, f'proc-{pod}')
        env[constants.SKY_RUNTIME_DIR_ENV_VAR_KEY] = shared
        env[constants.USER_ID_ENV_VAR] = _USER_HASH
        env['RACE_BASE'] = base
        env['RACE_POD'] = pod
        procs[pod] = subprocess.Popen(
            [sys.executable, script_path],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    # Wait for both processes to be imported and ready, then fire.
    for pod in ('a', 'b'):
        deadline = time.time() + 120
        while not os.path.exists(os.path.join(base, f'ready-{pod}')):
            if time.time() > deadline:
                for p in procs.values():
                    p.kill()
                raise RuntimeError(f'replica {pod} never became ready')
            time.sleep(0.01)
    with open(os.path.join(base, 'go'), 'w') as f:
        f.write('go')

    for pod, proc in procs.items():
        out, _ = proc.communicate(timeout=180)
        assert proc.returncode == 0, f'replica {pod} failed:\n{out}'

    # The DB row both processes must converge on.
    env_db = dict(os.environ)
    env_db['HOME'] = os.path.join(base, 'db-reader')
    env_db[constants.SKY_RUNTIME_DIR_ENV_VAR_KEY] = shared
    env_db[constants.USER_ID_ENV_VAR] = _USER_HASH
    res = subprocess.run(
        [
            sys.executable, '-c', 'from sky import global_user_state; '
            f'print(global_user_state.get_ssh_keys({_USER_HASH!r}))'
        ],
        env=env_db,
        check=True,
        capture_output=True,
        timeout=120,
        text=True,
    )
    # Debug log lines may precede the printed tuple; take the last line.
    db_pub, db_priv, exists = eval(res.stdout.strip().splitlines()[-1])
    assert exists

    def read_pub(pod):
        path = os.path.join(base, f'proc-{pod}', '.sky', 'clients', _USER_HASH,
                            'ssh', 'sky-key.pub')
        with open(path) as f:
            return f.read().strip()

    # Both local pairs and the database row must be the same pair.
    assert read_pub('a') == read_pub('b') == db_pub

    # A later call on either process keeps returning the converged pair.
    for pod in ('a', 'b'):
        env = dict(os.environ)
        env['HOME'] = os.path.join(base, f'proc-{pod}')
        env[constants.SKY_RUNTIME_DIR_ENV_VAR_KEY] = shared
        env[constants.USER_ID_ENV_VAR] = _USER_HASH
        res = subprocess.run(
            [
                sys.executable, '-c', 'from sky.utils import auth_utils; '
                'print(open(auth_utils.get_or_generate_keys()[1]).read().strip())'
            ],
            env=env,
            check=True,
            capture_output=True,
            timeout=120,
            text=True,
        )
        assert res.stdout.strip().splitlines()[-1] == db_pub


def test_get_or_generate_keys_converges_across_processes(tmp_path):
    for trial in range(2):
        _run_replicas(str(tmp_path / f'trial-{trial}'))
