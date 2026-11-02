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

# Printed by pod creation on each retry, as a provisioning warning that is
# streamed to the launch output.
_RETRY_WARNING = 'could not be called while creating a pod'


@pytest.mark.kubernetes
@pytest.mark.no_dependency
def test_kubernetes_launch_while_webhook_unavailable():
    """An unreachable webhook fails a launch only if it stays down.

    1. The webhook stays down: the launch fails after the retry budget, and
       the failover summary names the webhook rather than reporting a lack of
       resources.
    2. The webhook comes back while pod creation is retrying: the launch
       succeeds. The webhook is removed only once the retry warning has been
       printed, so the launch is known to have hit the outage.
    """
    name = smoke_tests_utils.get_cluster_name()
    name_down = f'{name}-down'
    # Starts with letters: --config parses the label value as YAML, and an
    # all-digit tag would become an int.
    tag = f'wh{uuid.uuid4().hex[:10]}'
    service = f'sky-{tag}'
    webhook = f'{service}.unavailable.skypilot.co'
    webhook_config = service
    label_key = 'skypilot-webhook-test'
    infra = smoke_tests_utils.AGENT_K8S_INFRA

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
    # Longer than it takes a launch to reach pod creation, so a missing
    # warning means the launch never hit the webhook, not that it was slow.
    warning_wait_seconds = 300
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w',
                                     delete=False) as manifest_f:
        manifest_f.write(manifest)
        manifest_f.flush()
        down_log = f'{name}-down.log'
        recover_log = f'{name}-recover.log'
        launch_args = (f'{infra} --config kubernetes.pod_config.metadata.'
                       f'labels.{label_key}={tag} --cpus 1 --memory 2')
        test = smoke_tests_utils.Test(
            'kubernetes_launch_while_webhook_unavailable',
            [
                f'kubectl apply -f {manifest_f.name}',
                # The webhook stays down for the whole launch.
                f'sky launch -y -c {name_down} {launch_args} '
                f'> {down_log} 2>&1; rc=$?; cat {down_log}; '
                f'echo "launch rc=$rc"; [ $rc -ne 0 ] && '
                f'grep -q "{_RETRY_WARNING}" {down_log} && '
                f'grep -q "Reason: Pod creation in context" {down_log} && '
                f'grep -q "admission webhook \'{webhook}\' could not be '
                f'called" {down_log}',
                # The webhook comes back while the launch is retrying.
                f': > {recover_log}; '
                f'( for i in $(seq {warning_wait_seconds}); do '
                f'grep -q "{_RETRY_WARNING}" {recover_log} && break; '
                f'sleep 1; done; '
                f'kubectl delete mutatingwebhookconfiguration {webhook_config} '
                f') & watcher=$!; '
                f'sky launch -y -c {name} {launch_args} '
                f'"echo webhook_recovered_ok" > {recover_log} 2>&1; rc=$?; '
                f'wait $watcher; cat {recover_log}; echo "launch rc=$rc"; '
                f'[ $rc -eq 0 ] && grep -q "{_RETRY_WARNING}" {recover_log} '
                f'&& grep -q webhook_recovered_ok {recover_log}',
            ],
            smoke_tests_utils.chain_teardown(
                f'sky down -y {name} || true',
                f'sky down -y {name_down} || true',
                f'kubectl delete mutatingwebhookconfiguration {webhook_config} '
                f'--ignore-not-found',
                f'kubectl delete service -n default {service} '
                f'--ignore-not-found', f'rm -f {manifest_f.name} {down_log} '
                f'{recover_log}'),
            timeout=smoke_tests_utils.get_timeout('kubernetes'),
        )
        smoke_tests_utils.run_one_test(test)
