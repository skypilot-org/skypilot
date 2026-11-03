"""Smoke test for pod creation while an admission webhook is unreachable.

A webhook with ``failurePolicy: Fail`` whose Service has no ready endpoints
makes the API server reject every pod create it matches with a 500
``failed calling webhook "<name>"``. Pod creation retries that for up to
60s and then fails with an error naming the webhook.

The test registers its own webhook instead of disturbing a real one (e.g.
Kueue's): a Service selecting no pods, and a MutatingWebhookConfiguration
pointing at it. Its ``objectSelector`` matches a label only this test's pods
carry, so other pods on a shared cluster are never sent to it.
"""
import tempfile
import textwrap
import uuid

import pytest
from smoke_tests import smoke_tests_utils

import sky

# Printed by pod creation on each retry, as a provisioning warning in the
# jobs controller log.
_RETRY_WARNING = 'could not be called while creating a pod'

_wait_cmd = (smoke_tests_utils.
             get_cmd_wait_until_managed_job_status_contains_matching_job_name)


def _wait_for_job_end(job_name: str, expected: str, timeout: int) -> str:
    """A step that waits for a managed job to end and checks its status.

    Waits for any terminal status, so a job that ends some other way fails
    the step at once instead of at the timeout.
    """
    wait = _wait_cmd(job_name=job_name,
                     job_status=sky.ManagedJobStatus.terminal_statuses(),
                     timeout=timeout)
    return (f'{wait} && q=$(sky jobs queue) && echo "$q" && '
            f'echo "$q" | grep {job_name} | grep -w {expected}')


@pytest.mark.kubernetes
@pytest.mark.managed_jobs
@pytest.mark.no_dependency
def test_kubernetes_jobs_launch_while_webhook_unavailable():
    """An unreachable webhook keeps a managed job from starting only while it
    stays down.

    1. The webhook stays down: each launch attempt fails after the retry
       budget with a reason naming the webhook, until
       job.wait_for_scheduling_timeout cancels the job.
    2. The webhook comes back while pod creation is retrying: the job runs.
       The webhook is removed only once the retry warning is in the
       controller log, so the launch is known to have hit the outage.
    """
    if not smoke_tests_utils.server_side_is_consolidation_mode():
        pytest.skip('Needs consolidation mode: with a separate controller, the '
                    'controller pod carries the test label too, so the webhook '
                    'blocks the controller launch rather than the job.')

    name = smoke_tests_utils.get_cluster_name()
    job_down = f'{name}-down'
    job_up = f'{name}-up'
    # Starts with letters: --config parses the label value as YAML, and an
    # all-digit tag would become an int.
    tag = f'wh{uuid.uuid4().hex[:10]}'
    service = f'sky-{tag}'
    webhook = f'{service}.unavailable.skypilot.co'
    webhook_config = service
    label_key = 'skypilot-webhook-test'

    manifest = textwrap.dedent(f"""\
        apiVersion: v1
        kind: Service
        metadata:
          name: {service}
          namespace: default
        spec:
          selector:
            {label_key}-backend: {tag}
          ports:
          - port: 443
            targetPort: 8443
        ---
        apiVersion: admissionregistration.k8s.io/v1
        kind: MutatingWebhookConfiguration
        metadata:
          name: {webhook_config}
        webhooks:
        - name: {webhook}
          clientConfig:
            service:
              namespace: default
              name: {service}
              path: /mutate
              port: 443
          rules:
          - operations: [CREATE]
            apiGroups: ['']
            apiVersions: [v1]
            resources: [pods]
          objectSelector:
            matchLabels:
              {label_key}: {tag}
          failurePolicy: Fail
          sideEffects: None
          admissionReviewVersions: [v1]
          timeoutSeconds: 5
    """)
    # Longer than one launch attempt's 60s of pod creation retries, so the
    # controller has logged the webhook reason before the job is cancelled.
    down_task = textwrap.dedent("""\
        job:
          wait_for_scheduling_timeout: 120s
        run: echo should_not_run
    """)
    # Longer than it takes a job to reach pod creation, so a missing warning
    # means the job never hit the webhook, not that it was slow.
    warning_wait_seconds = 300
    poll_seconds = 3
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w',
                                     delete=False) as manifest_f, \
         tempfile.NamedTemporaryFile(suffix='.yaml', mode='w',
                                     delete=False) as down_task_f:
        manifest_f.write(manifest)
        manifest_f.flush()
        down_task_f.write(down_task)
        down_task_f.flush()
        launch_args = (f'{smoke_tests_utils.AGENT_K8S_INFRA} '
                       f'--config kubernetes.pod_config.metadata.labels.'
                       f'{label_key}={tag} --cpus 1 --memory 2')
        down_log = f'{job_down}-controller.log'
        up_log = f'{job_up}-controller.log'
        test = smoke_tests_utils.Test(
            'kubernetes_jobs_launch_while_webhook_unavailable',
            [
                f'kubectl apply -f {manifest_f.name}',
                # The webhook stays down.
                f'sky jobs launch -y -d -n {job_down} {launch_args} '
                f'{down_task_f.name}',
                _wait_for_job_end(job_down, 'CANCELLED', timeout=400),
                f'sky jobs logs --controller -n {job_down} --no-follow '
                f'> {down_log} 2>&1; cat {down_log}; '
                f'grep -q "{_RETRY_WARNING}" {down_log} && '
                f'grep -q "Reason: Pod creation in context" {down_log} && '
                f'grep -q "admission webhook \'{webhook}\' could not be '
                f'called" {down_log}',
                # The webhook comes back while the launch is retrying.
                f'sky jobs launch -y -d -n {job_up} {launch_args} '
                f'"echo webhook_recovered_ok"',
                f'start=$SECONDS; while (( SECONDS - start < '
                f'{warning_wait_seconds} )); do '
                f'sky jobs logs --controller -n {job_up} --no-follow '
                f'> {up_log} 2>&1; grep -q "{_RETRY_WARNING}" {up_log} && '
                f'break; sleep {poll_seconds}; done; cat {up_log}; '
                f'grep -q "{_RETRY_WARNING}" {up_log} && '
                f'kubectl delete mutatingwebhookconfiguration '
                f'{webhook_config}',
                _wait_for_job_end(job_up, 'SUCCEEDED', timeout=400),
                f'jid=$(sky jobs queue | grep {job_up} | head -1 | '
                f'awk \'{{print $1}}\') && echo "job id: $jid" && '
                f'out=$(sky jobs logs "$jid" --no-follow 2>&1); '
                f'echo "$out"; echo "$out" | grep -q webhook_recovered_ok',
            ],
            smoke_tests_utils.chain_teardown(
                f'sky jobs cancel -y -n {job_down} || true',
                f'sky jobs cancel -y -n {job_up} || true',
                f'kubectl delete mutatingwebhookconfiguration {webhook_config} '
                f'--ignore-not-found',
                f'kubectl delete service -n default {service} '
                f'--ignore-not-found',
                f'rm -f {manifest_f.name} {down_task_f.name} {down_log} '
                f'{up_log}'),
            timeout=smoke_tests_utils.get_timeout('kubernetes'),
        )
        smoke_tests_utils.run_one_test(test)
