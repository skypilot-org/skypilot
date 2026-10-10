"""Smoke tests for SkyPilot log collection to an OTLP endpoint."""

import shlex
import tempfile
import textwrap
from typing import List
import uuid

import pytest
from smoke_tests import smoke_tests_utils

_OTELCOL_VERSION = '0.161.0'
# Where the collector's file exporter writes received logs, as OTLP JSON lines.
_COLLECTOR_OUTPUT = '~/otel.jsonl'

# An OpenTelemetry Collector that accepts OTLP/HTTP with a bearer token and
# writes every log it receives to _COLLECTOR_OUTPUT, so the test can assert on
# exactly what reached the endpoint.
_COLLECTOR_TASK = textwrap.dedent(f"""\
    resources:
      infra: aws
      cpus: 2+
      memory: 4+
      ports: 4318
    setup: |
      set -e
      arch=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')
      curl -fsSL -o /tmp/otelcol.tar.gz https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v{_OTELCOL_VERSION}/otelcol-contrib_{_OTELCOL_VERSION}_linux_$arch.tar.gz
      mkdir -p ~/otelcol
      tar -xzf /tmp/otelcol.tar.gz -C ~/otelcol otelcol-contrib
    run: |
      cat > ~/otelcol/config.yaml <<'EOF'
      extensions:
        bearertokenauth:
          token: ${{env:OTEL_TOKEN}}
      receivers:
        otlp:
          protocols:
            http:
              endpoint: 0.0.0.0:4318
              auth:
                authenticator: bearertokenauth
      exporters:
        file:
          path: {_COLLECTOR_OUTPUT.replace('~', '${env:HOME}')}
          flush_interval: 1s
      service:
        extensions: [bearertokenauth]
        pipelines:
          logs:
            receivers: [otlp]
            exporters: [file]
      EOF
      exec ~/otelcol/otelcol-contrib --config ~/otelcol/config.yaml
    """)

# Emits the markers the test looks for, including lines that are awkward for a
# log pipeline. A line longer than the tail buffer must be skipped without
# stopping the rest of the log from being forwarded.
_LOG_TASK = textwrap.dedent("""\
    run: |
      for i in $(seq 1 10); do echo "otlp-marker-$MARK-$i"; done
      printf 'otlp-marker-%s-utf8 caf\\xc3\\xa9 \\xff\\xfe end\\n' "$MARK"
      printf '\\033[31motlp-marker-%s-ansi\\033[0m\\n' "$MARK"
      echo "otlp-marker-$MARK-stderr" >&2
      python3 -c "import os; print('otlp-marker-' + os.environ['MARK'] + '-long-' + 'x' * 100000)"
      python3 -c "import os; print('otlp-marker-' + os.environ['MARK'] + '-huge-' + 'y' * 400000)"
      echo "otlp-marker-$MARK-after-long"
    """)


def _expected_markers(mark: str) -> List[str]:
    # Quoted as they appear in the OTLP JSON bodies, so that e.g. marker 1 does
    # not also match marker 10.
    markers = [f'"otlp-marker-{mark}-{i}"' for i in range(1, 11)]
    markers += [
        f'otlp-marker-{mark}-utf8',
        f'otlp-marker-{mark}-ansi',
        f'"otlp-marker-{mark}-stderr"',
        f'otlp-marker-{mark}-long-xxxx',
        f'"otlp-marker-{mark}-after-long"',
    ]
    return markers


def _wait_in_collector_cmd(collector: str,
                           patterns: List[str],
                           timeout: int = 180) -> str:
    """Waits until every pattern shows up in the collector's received logs."""
    checks = ' && '.join(
        f'grep -qF -- {shlex.quote(p)} {_COLLECTOR_OUTPUT}' for p in patterns)
    missing = '; '.join(f'grep -qF -- {shlex.quote(p)} {_COLLECTOR_OUTPUT} || '
                        f'echo "missing: "{shlex.quote(p)}' for p in patterns)
    return (f'start=$(date +%s); '
            f'until ssh {collector} {shlex.quote(checks)}; do '
            f'  if [ $(( $(date +%s) - start )) -gt {timeout} ]; then '
            f'    ssh {collector} {shlex.quote(missing)}; exit 1; '
            f'  fi; '
            f'  sleep 5; '
            f'done')


def _absent_in_collector_cmd(collector: str, pattern: str) -> str:
    # Checked remotely so that a failed ssh does not pass as "absent".
    check = (f'test -f {_COLLECTOR_OUTPUT} && '
             f'! grep -qF -- {shlex.quote(pattern)} {_COLLECTOR_OUTPUT}')
    return f'ssh {collector} {shlex.quote(check)}'


def _ip_cmd(cluster: str) -> str:
    # SKYPILOT_DEBUG=0: debug logs go to stdout and would pollute the output.
    return f'$(SKYPILOT_DEBUG=0 sky status --ip {cluster} | tail -n 1)'


def _write_config_cmd(collector: str, config_path: str, headers_path: str,
                      case: str) -> str:
    """Writes a logs.store=otlp config pointing at the collector's IP."""
    config = textwrap.dedent(f"""\
        logs:
          store: otlp
          otlp:
            endpoint: http://$ip:4318
            headers_file: {headers_path}
            resource_attributes:
              skypilot_smoke_test_case: {case}
        """)
    return (f'ip={_ip_cmd(collector)} && '
            f'[ -n "$ip" ] && '
            f'cat > {config_path} <<EOF\n{config}EOF')


@pytest.mark.no_vast  # Requires AWS
@pytest.mark.no_shadeform  # Requires AWS
@pytest.mark.no_fluidstack  # Requires AWS to be enabled
@pytest.mark.no_nebius  # Requires AWS to be enabled
@pytest.mark.no_seeweb  # Requires AWS to be enabled
# logs.otlp.headers_file is a path on the API server.
@pytest.mark.no_remote_server
def test_log_collection_to_otlp(generic_cloud: str):
    """Forwards cluster and managed job logs to a real OTLP collector.

    Also checks that a rejected credential neither delivers logs nor fails the
    user's job.
    """
    name = smoke_tests_utils.get_cluster_name()
    collector = f'{name}-otel'
    bad = f'{name}-bad'
    job = f'{name}-job'
    token = uuid.uuid4().hex
    with tempfile.NamedTemporaryFile('w', suffix='.yaml') as collector_task, \
        tempfile.NamedTemporaryFile('w', suffix='.yaml') as log_task, \
        tempfile.NamedTemporaryFile('w') as good_headers, \
        tempfile.NamedTemporaryFile('w') as bad_headers, \
        tempfile.NamedTemporaryFile('w', suffix='.yaml') as good_config, \
        tempfile.NamedTemporaryFile('w', suffix='.yaml') as bad_config:
        collector_task.write(_COLLECTOR_TASK)
        collector_task.flush()
        log_task.write(_LOG_TASK)
        log_task.flush()
        good_headers.write(
            f'# Test credential\nAuthorization: Bearer {token}\n')
        good_headers.flush()
        bad_headers.write('Authorization: Bearer not-the-token\n')
        bad_headers.flush()

        def launch(cluster: str, config_path: str, mark: str) -> str:
            return smoke_tests_utils.with_config(
                f'sky launch -y -c {cluster} --infra {generic_cloud} '
                f'{smoke_tests_utils.LOW_RESOURCE_ARG} --env MARK={mark} '
                f'{log_task.name}', config_path)

        test = smoke_tests_utils.Test(
            'log_collection_to_otlp',
            [
                # Start the collector and wait until it answers. A request
                # without the token is rejected with 401 once it is serving.
                (f'sky launch -y -d -c {collector} --env OTEL_TOKEN={token} '
                 f'{collector_task.name}'),
                (f'ip={_ip_cmd(collector)} && '
                 'for i in $(seq 1 60); do '
                 '  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST '
                 '    http://$ip:4318/v1/logs); '
                 '  [ "$code" = 401 ] && exit 0; sleep 5; '
                 'done; exit 1'),
                _write_config_cmd(collector, good_config.name,
                                  good_headers.name, f'{name}-case'),
                _write_config_cmd(collector, bad_config.name, bad_headers.name,
                                  f'{name}-bad-case'),
                # Cluster job logs, with the cluster identity and the
                # configured attributes sent as OTLP resource attributes.
                launch(name, good_config.name, 'good'),
                _wait_in_collector_cmd(
                    collector,
                    _expected_markers('good') + [
                        '{"key":"service.name","value":'
                        '{"stringValue":"skypilot"}}',
                        '{"key":"skypilot.cluster_name","value":'
                        f'{{"stringValue":"{name}"}}}}',
                        '{"key":"skypilot_smoke_test_case","value":'
                        f'{{"stringValue":"{name}-case"}}}}',
                    ]),
                _absent_in_collector_cmd(collector, 'otlp-marker-good-huge'),
                # Managed job logs. The job cluster is launched by the jobs
                # controller, which must pick up the delivered headers file.
                smoke_tests_utils.with_config(
                    f'sky jobs launch -y -n {job} --infra {generic_cloud} '
                    f'{smoke_tests_utils.LOW_RESOURCE_ARG} --env MARK=job '
                    f'{log_task.name}', good_config.name),
                _wait_in_collector_cmd(collector, _expected_markers('job')),
                # A rejected credential: the job still succeeds (sky launch
                # exits 0), fluent-bit reports the 401, and nothing arrives.
                launch(bad, bad_config.name, 'bad'),
                (f'for i in $(seq 1 24); do '
                 f'  ssh {bad} "grep -q \'HTTP status=401\' /tmp/fluentbit.log"'
                 f'  && exit 0; sleep 5; '
                 f'done; ssh {bad} "cat /tmp/fluentbit.log"; exit 1'),
                _absent_in_collector_cmd(collector, 'otlp-marker-bad-'),
            ],
            (f'sky jobs cancel -y -n {job}; '
             f'sky down -y {name} {bad} {collector}'),
            timeout=smoke_tests_utils.LOG_STORE_CMD_TIMEOUT,
        )
        smoke_tests_utils.run_one_test(test)
