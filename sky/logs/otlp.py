"""OpenTelemetry (OTLP) logging agent."""

import os
import re
import shlex
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

import pydantic

from sky import exceptions
from sky.logs.agent import FluentbitAgent
from sky.skylet import constants
from sky.utils import resources_utils

# Remote path where the user-provided headers file (logs.otlp.headers_file) is
# delivered. Fixed and home-relative so it is readable by the cluster's runtime
# user regardless of where the file lives on the API server.
_REMOTE_HEADERS_PATH = os.path.join(constants.LOGGING_CONFIG_DIR,
                                    'otlp_headers')
# Prefix of the env vars that carry the values of headers read from
# headers_file. fluent-bit expands ${VAR} in its config, so secret header
# values never touch the (world-readable) config file on the cluster.
_HEADER_ENV_PREFIX = 'SKYPILOT_OTLP_HEADER_'
# RFC 9110 field-name token characters.
_HEADER_NAME_RE = re.compile(r'^[A-Za-z0-9!#$%&\'*+.^_`|~-]+$')
# Mirrors _parse_headers_file on the cluster: prints the value of the N-th
# header line (1-based, skipping blank and comment lines) of the headers file.
_AWK_HEADER_VALUE = (
    'NF && $0 !~ /^[[:space:]]*#/ {c++; if (c == n) '
    '{sub(/^[^:]*:[[:space:]]*/, ""); sub(/[[:space:]]+$/, ""); print; exit}}')

_PROTOCOL_HTTP = 'http/protobuf'
_PROTOCOL_GRPC = 'grpc'


class _OtlpLoggingConfig(pydantic.BaseModel):
    """Configuration for the OTLP logging agent."""
    endpoint: str
    protocol: str = _PROTOCOL_HTTP
    headers: Optional[Dict[str, str]] = None
    headers_file: Optional[str] = None
    compression: str = 'none'
    tls_verify: bool = True
    resource_attributes: Optional[Dict[str, str]] = None


def _parse_headers_file(path: str) -> List[str]:
    """Returns the header names in a headers file, in file order.

    The file holds one ``Name: value`` header per line; blank lines and lines
    starting with ``#`` are ignored.
    """
    names = []
    with open(path, 'r', encoding='utf-8') as f:
        for lineno, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            name, sep, _ = stripped.partition(':')
            name = name.strip()
            if not sep or not _HEADER_NAME_RE.match(name):
                raise exceptions.InvalidSkyPilotConfigError(
                    f'Invalid header on line {lineno} of logs.otlp.headers_file'
                    f' {path!r}: expected `Name: value`.')
            names.append(name)
    return names


class OtlpLoggingAgent(FluentbitAgent):
    """Forwards logs to an OpenTelemetry (OTLP) endpoint.

    Logs are shipped with fluent-bit's `opentelemetry` output to any OTLP
    compatible receiver, e.g. an OpenTelemetry Collector or an observability
    vendor's OTLP ingest endpoint.

    Example configuration:
    ```yaml
    logs:
      store: otlp
      otlp:
        endpoint: https://otel-collector.example.com:4318
        headers_file: ~/.sky/otlp_headers  # `Authorization: Bearer <token>`
        resource_attributes:
          deployment.environment: production
    ```
    """

    def __init__(self, config: Dict[str, Any]):
        try:
            self.config = _OtlpLoggingConfig(**config)
        except pydantic.ValidationError as e:
            raise exceptions.InvalidSkyPilotConfigError(
                f'Invalid logs.otlp config: {e}') from e
        # The config schema accepts these case-insensitively.
        self.config.protocol = self.config.protocol.lower()
        self.config.compression = self.config.compression.lower()
        if self.config.protocol not in (_PROTOCOL_HTTP, _PROTOCOL_GRPC):
            raise exceptions.InvalidSkyPilotConfigError(
                f'Invalid logs.otlp.protocol {self.config.protocol!r}: '
                f'expected {_PROTOCOL_HTTP!r} or {_PROTOCOL_GRPC!r}.')
        if self.config.compression not in ('none', 'gzip'):
            raise exceptions.InvalidSkyPilotConfigError(
                f'Invalid logs.otlp.compression {self.config.compression!r}: '
                'expected \'none\' or \'gzip\'.')
        parsed = urllib.parse.urlparse(self.config.endpoint)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise exceptions.InvalidSkyPilotConfigError(
                f'Invalid logs.otlp.endpoint {self.config.endpoint!r}: '
                'expected a URL like https://otel-collector.example.com:4318.')
        self._parsed_endpoint = parsed
        super().__init__()

    def _headers_file_local_path(self) -> Optional[str]:
        if not self.config.headers_file:
            return None
        # expanduser handles '~', and realpath resolves symlinks (e.g. a
        # Kubernetes Secret mounted as a volume) and relative paths.
        local_path = os.path.realpath(
            os.path.expanduser(self.config.headers_file))
        delivered_path = os.path.expanduser(_REMOTE_HEADERS_PATH)
        if not os.path.exists(local_path) and os.path.exists(delivered_path):
            # A node that itself launches clusters (e.g. a jobs controller)
            # shares the API server's config, whose headers_file path does not
            # exist there; use the copy delivered to it instead.
            return delivered_path
        return local_path

    def _target(self) -> Tuple[str, int, str, bool]:
        """Returns (host, port, logs_uri, tls) for the OTLP endpoint."""
        parsed = self._parsed_endpoint
        tls = parsed.scheme == 'https'
        assert parsed.hostname is not None
        port = parsed.port or (443 if tls else 80)
        # Like OTEL_EXPORTER_OTLP_ENDPOINT, the endpoint is a base URL and the
        # signal path is appended, preserving any path prefix (e.g. a vendor
        # gateway mounted under /otlp). gRPC routes by service method instead.
        logs_uri = parsed.path.rstrip('/') + '/v1/logs'
        return parsed.hostname, port, logs_uri, tls

    def get_setup_command(self,
                          cluster_name: resources_utils.ClusterName) -> str:
        setup = super().get_setup_command(cluster_name)
        local_path = self._headers_file_local_path()
        if local_path is None:
            return setup
        # Export each header value from the delivered headers file so that
        # fluent-bit expands it from the environment at startup.
        exports = []
        for i in range(len(_parse_headers_file(local_path))):
            awk = (f'awk -v n={i + 1} {shlex.quote(_AWK_HEADER_VALUE)} '
                   f'{_REMOTE_HEADERS_PATH}')
            exports.append(f'export {_HEADER_ENV_PREFIX}{i}="$({awk})"')
        if not exports:
            return setup
        # The file holds secrets; keep it readable by the runtime user only.
        return (f'chmod 600 {_REMOTE_HEADERS_PATH}; ' + '; '.join(exports) +
                '; ' + setup)

    def fluentbit_output_config(
            self, cluster_name: resources_utils.ClusterName) -> Dict[str, Any]:
        host, port, logs_uri, tls = self._target()
        config: Dict[str, Any] = {
            'name': 'opentelemetry',
            'match': '*',
            'host': host,
            'port': port,
            'logs_uri': logs_uri,
            'tls': 'on' if tls else 'off',
            # Send the log line as the record body and the remaining fields
            # (e.g. log_path) as log record attributes.
            'logs_body_key': '$log',
            'logs_body_key_attributes': 'true',
        }
        if tls:
            config['tls.verify'] = 'on' if self.config.tls_verify else 'off'
        if self.config.protocol == _PROTOCOL_GRPC:
            config['grpc'] = 'on'
        if self.config.compression != 'none':
            config['compress'] = self.config.compression
        headers = [f'{k} {v}' for k, v in (self.config.headers or {}).items()]
        local_path = self._headers_file_local_path()
        if local_path is not None:
            for i, name in enumerate(_parse_headers_file(local_path)):
                headers.append(f'{name} ${{{_HEADER_ENV_PREFIX}{i}}}')
        if headers:
            config['header'] = headers

        attributes = {
            'service.name': 'skypilot',
            'skypilot.cluster_name': cluster_name.display_name,
            'skypilot.cluster_id': cluster_name.name_on_cloud,
            **(self.config.resource_attributes or {}),
        }
        # Wrap records in an OTLP envelope so the cluster identity is sent as
        # resource attributes, which OTLP backends index per stream rather than
        # repeating on every log record. Attached to the output rather than the
        # tail input, since the parser filter in between drops the envelope.
        processors: List[Dict[str, Any]] = [{'name': 'opentelemetry_envelope'}]
        for key, value in attributes.items():
            processors.append({
                'name': 'content_modifier',
                'context': 'otel_resource_attributes',
                'action': 'upsert',
                'key': key,
                'value': value,
            })
        config['processors'] = {'logs': processors}
        return config

    def get_credential_file_mounts(self) -> Dict[str, str]:
        local_path = self._headers_file_local_path()
        if local_path is None:
            return {}
        return {_REMOTE_HEADERS_PATH: local_path}
