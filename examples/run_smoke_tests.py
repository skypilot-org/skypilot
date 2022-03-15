"""Smoke tests, run in parallel.

Usage:

    # Run from repo root dir:
    time python -u examples/run_smoke.py 2>&1 | tee run.log

Implemented behaviors:

* Launch all tests in parallel in their own process.  Logs for each test are
  redirected to its own file.

* Each test has a (per-command) timeout.  Timing out is treated as a failure.

* When any test failed:
  * an error message is printed
  * its cluster is torn down
  * this script & the rest of the tests continue
  * this script's final exit code will be 1

* When any test passed:
  * its cluster is torn down

* When all tests passed:
  * this script exits with 0

Note to developers:
  * To add a test: append to the _SMOKE_TESTS list.
  * Certain tests (e.g., tpu_app) may need longer timeouts than the default.

Tests:

  * Successful scenarios
    - time python -u examples/run_smoke.py 2>&1 | tee run.log
    - 31:23.62 total
    - except: tpu_app (name bug)


  * Erroneous scenarios

    * [env_check, minimal (with exit 1 in setup)]

    * [minimal (with exit 1 in setup), env_check]

    * [minimal (with exit 1 in run), env_check]

FIXME:

 - tpu_app less '/var/folders/8f/56gzvwkd3n3293xjlrztr6600000gp/T/tpu_app-cassduyg.log'
   - tpu_name bug + we don't have a way to query exitcode

Future TODOs
 - sky logs --status cluster 1
 - at the end of this script, concat all .log into one file so we can view it
   to ensure nothing is wrong (or auto grep for error/exception etc.)

----

Known issues

cannot ctrl-c the terminal that launches this script -- during or after the execution.
- workaround: kill the window,
 (and if you want to ctrl-c in the middle) just run 'sky down -a -y' and the tests will error out (assuming you have no other clusters) manually

Our logging would mess up spacing (related to new line replacement?) after an error occurs / or just after a while
- workaround: pipe to a file and view

 python -u run_smoke.py 2>&1  142.87s user 85.26s system 12% cpu 31:23.62 total
"""
import subprocess
import sys
import tempfile
import time
from typing import List, Optional, Tuple, NamedTuple

import colorama
import ray

from sky.backends import backend_utils


class Test(NamedTuple):
    name: str
    # Each command is executed serially.  If any failed, the remaining commands
    # are not run and the test is treated as failed.
    commands: List[str]
    teardown: Optional[str] = None
    # Timeout for each command in seconds.
    timeout: int = 15 * 60
    # timeout: int = 10


# In approximately most expensive to least order to slightly optimize for
# stragglers.
_SMOKE_TESTS = [
    # ---------- Dry run: 2 Tasks in a chain. ----------
    Test(
        'example_app',
        ['python examples/example_app.py'],
    ),
    # ---------- Testing Azure start and stop instances ----------
    Test(
        'azure-start-stop',
        [
            'sky launch -y -c azure-start-stop examples/azure_start_stop.yaml',
            'sky exec azure-start-stop examples/azure_start_stop.yaml',
            'sky stop -y azure-start-stop',
            'sky start -y azure-start-stop',
            'sky exec azure-start-stop examples/azure_start_stop.yaml',
        ],
        'sky down -y azure-start-stop',
        timeout=30 * 60,  # 30 mins  (it takes around ~23 mins)
    ),
    # ---------- Testing GCP start and stop instances ----------
    Test(
        'gcp-start-stop',
        [
            'sky launch -y -c gcp-start-stop examples/gcp_start_stop.yaml',
            'sky exec gcp-start-stop examples/gcp_start_stop.yaml',
            'sky stop -y gcp-start-stop',
            'sky start -y gcp-start-stop',
            'sky exec gcp-start-stop examples/gcp_start_stop.yaml',
        ],
        'sky down -y gcp-start-stop',
    ),
    # ---------- Task: n=2 nodes with setups. ----------
    Test(
        'resnet_distributed_tf_app',
        [
            # NOTE: running it twice will hang (sometimes?) - an app-level bug.
            'python examples/resnet_distributed_tf_app.py',
        ],
        'sky down -y dtf',
        timeout=25 * 60,  # 25 mins (it takes around ~19 mins)
    ),
    # ---------- file_mounts ----------
    Test(
        'using_file_mounts',
        [
            'touch ~/tmpfile',
            'mkdir -p ~/tmp-workdir',
            'touch ~/tmp-workdir/foo',
            'sky launch -y -c fm examples/using_file_mounts.yaml',
        ],
        'sky down -y fm',
        timeout=20 * 60,  # 20 mins
    ),
    # ---------- Job Queue. ----------
    Test(
        'job_queue_multinode',
        [
            'sky launch -y -c mjq examples/job_queue/cluster_multinode.yaml',
            'sky exec mjq -d examples/job_queue/job_multinode.yaml',
            'sky exec mjq -d examples/job_queue/job_multinode.yaml',
            'sky exec mjq -d examples/job_queue/job_multinode.yaml',
            'sky cancel mjq 1',
            'sky logs mjq 2',
            'sky queue mjq',
        ],
        'sky down -y mjq',
    ),
    Test(
        'job_queue',
        [
            'sky launch -y -c jq examples/job_queue/cluster.yaml',
            'sky exec jq -d examples/job_queue/job.yaml',
            'sky exec jq -d examples/job_queue/job.yaml',
            'sky exec jq -d examples/job_queue/job.yaml',
            'sky logs jq 2',
            'sky queue jq',
        ],
        'sky down -y jq',
    ),
    # ---------- Task: 1 node training. ----------
    Test(
        'huggingface_glue_imdb_app',
        [
            ('sky launch -y -c huggingface '
             'examples/huggingface_glue_imdb_app.yaml'),
            'sky exec huggingface examples/huggingface_glue_imdb_app.yaml',
        ],
        'sky down -y huggingface',
    ),
    # ---------- TPU. ----------
    Test(
        'tpu_app',
        ['sky launch -y -c tpu examples/tpu_app.yaml'],
        'sky down -y tpu',
    ),
    # ---------- Check Sky's environment variables; workdir. ----------
    Test(
        'env_check',
        ['sky launch -y -c env examples/env_check.yaml'],
        'sky down -y env',
    ),
    # ---------- Simple apps. ----------
    Test(
        'multi_hostname',
        [
            'sky launch -y -c mh examples/multi_hostname.yaml',
            'sky exec mh examples/multi_hostname.yaml',
        ],
        'sky down -y mh',
    ),
    Test(
        'minimal',
        ['sky launch -y -c min examples/minimal.yaml'],
        'sky down -y min',
    ),
    # ---------- Submitting multiple tasks to the same cluster.. ----------
    Test(
        'multi_echo',
        ['python examples/multi_echo.py'],
        'sky down -y multi-echo',
    ),
]


def run_one_test(test: Test) -> Tuple[int, str, str]:
    # FIXME(zongheng,suquark): starting all tests (almost) together fails
    # backend_utils#write_cluster_config() -> wheel_utils#build_sky_wheel().
    for i in range(len(_SMOKE_TESTS)):
        if test == _SMOKE_TESTS[i]:
            time.sleep(i * 4)

    log_file = tempfile.NamedTemporaryFile('a',
                                           prefix=f'{test.name}-',
                                           suffix='.log',
                                           delete=False)

    print(f'{test.name}: per-command timeout'
          f'={test.timeout} seconds.')
    print(f'  tail -f -n100 {log_file.name}')
    for command in test.commands:
        print(f'  {command}')
        proc = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            shell=True,
        )

        message = (f'{test.name}: test command exited with non-zero status: '
                   f'{command}')
        error_occurred = (
            f'{colorama.Fore.RED}{message}{colorama.Style.RESET_ALL}')
        try:
            outs, errs = proc.communicate(timeout=test.timeout)
        except subprocess.TimeoutExpired as e:
            log_file.flush()
            print(e)
            print(error_occurred)
            proc.returncode = 1  # None if we don't set it.

            # raise e  # Raise = retry
            break  # no retry

        finally:
            log_file.flush()
            proc.kill()

        if proc.returncode:
            print(error_occurred)
            break

    outcome = 'failed' if proc.returncode else 'succeeded'
    print(f'{test.name} {outcome}. Log: less {log_file.name}')
    if test.teardown is not None:
        print(f'Teardown: {test.teardown}')
        backend_utils.run(
            test.teardown,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            timeout=10 * 60,  # 10 mins
        )

    return proc.returncode, test.name, log_file.name


def main():
    status = 0

    ray.init()
    tasks = [
        ray.remote(run_one_test).options(
            num_cpus=0.1,
            # TODO(zongheng,suquark): Necessary for wheel building
            # conflicts. Remove the retry flag here after we allow parallel
            # wheel builds.
            retry_exceptions=True,
            max_retries=1,
            name=test.name,
        ).remote(test) for test in _SMOKE_TESTS
    ]

    failed_tests = []
    failed_tests_logs = []
    succeeded_tests = []
    while tasks:
        ready_refs, remaining_refs = ray.wait(tasks,
                                              num_returns=1,
                                              timeout=None)
        tasks = remaining_refs
        results = ray.get(ready_refs)
        for f in results:
            failed, name, log = f
            if failed:
                status = 1
                failed_tests.append(name)
                failed_tests_logs.append(log)
            else:
                succeeded_tests.append(name)

        failed_str = '' if not failed_tests else f' ({",".join(failed_tests)})'
        succeeded_str = ('' if not succeeded_tests else
                         f' ({",".join(succeeded_tests)})')
        print('Tests: '
              f'{len(failed_tests)} failed{failed_str}, '
              f'{len(succeeded_tests)} succeeded{succeeded_str}, '
              f'{len(remaining_refs)} remaining')

    if failed_tests:
        print(f'*** {len(failed_tests)} failed tests ***')
        for name, log in zip(failed_tests, failed_tests_logs):
            print(f'{name}: less {log}')
    else:
        print('*** All tests succeeded. ***')

    sys.exit(status)


if __name__ == '__main__':
    main()
