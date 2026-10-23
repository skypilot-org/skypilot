"""OpenTelemetry (OTLP) logging agent."""

import hashlib
import os
import re
import shlex
import tempfile
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
# Prints the value of header N (1-based) of a canonical headers file, which has
# exactly one `Name: value` line per header (see _stage_headers_file).
_AWK_HEADER_VALUE = 'NR == n {sub(/^[^:]*: /, ""); print; exit}'

_PROTOCOL_HTTP = 'http/protobuf'
_PROTOCOL_GRPC = 'grpc'


class _TlsConfig(pydantic.BaseModel):
    """Client TLS settings, named after the OpenTelemetry Collector's."""
    insecure_skip_verify: bool = False


class _OtlpLoggingConfig(pydantic.BaseModel):
    """Configuration for the OTLP logging agent."""
    endpoint: str
    protocol: str = _PROTOCOL_HTTP
    headers: Optional[Dict[str, str]] = None
    headers_file: Optional[str] = None
    compression: str = 'none'
    tls: _TlsConfig = _TlsConfig()
    resource_attributes: Optional[Dict[str, str]] = None


def _parse_headers_file(path: str) -> List[Tuple[str, str]]:
    """Returns the (name, value) headers in a headers file, in file order.

    The file holds one ``Name: value`` header per line; blank lines and lines
    starting with ``#`` are ignored, as is surrounding whitespace (including
    the ``\\r`` of CRLF line endings).
    """
    headers = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise exceptions.InvalidSkyPilotConfigError(
            f'Failed to read logs.otlp.headers_file {path!r}: {e}') from e
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        name, sep, value = stripped.partition(':')
        name = name.strip()
        if not sep or not _HEADER_NAME_RE.match(name):
            raise exceptions.InvalidSkyPilotConfigError(
                f'Invalid header on line {lineno} of logs.otlp.headers_file'
                f' {path!r}: expected `Name: value`.')
        headers.append((name, value.strip()))
    return headers


def _stage_headers_file(path: str) -> Tuple[str, List[str]]:
    """Writes a canonical, owner-only copy of a headers file.

    The copy has exactly one `Name: value` line per header, so the cluster can
    read header N as line N without re-parsing the user's file, and mode 0600,
    which the upload (rsync -a) carries over to the cluster. It is named after
    its content, so concurrent launches with different files do not collide.

    Returns:
        The path of the copy, and the header names in order.
    """
    headers = _parse_headers_file(path)
    content = ''.join(f'{name}: {value}\n' for name, value in headers)
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]
    staging_dir = os.path.expanduser(constants.LOGGING_CONFIG_DIR)
    staged_path = os.path.join(staging_dir, f'otlp_headers-{digest}')
    if not os.path.exists(staged_path):
        os.makedirs(staging_dir, mode=0o700, exist_ok=True)
        # mkstemp creates the file with mode 0600; rename it into place
        # atomically so a concurrent reader never sees a partial file.
        fd, tmp_path = tempfile.mkstemp(dir=staging_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(tmp_path, staged_path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    return staged_path, [name for name, _ in headers]


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
        invalid_endpoint = exceptions.InvalidSkyPilotConfigError(
            f'Invalid logs.otlp.endpoint {self.config.endpoint!r}: '
            'expected a URL like https://otel-collector.example.com:4318.')
        parsed = urllib.parse.urlparse(self.config.endpoint)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise invalid_endpoint
        try:
            # urllib only validates the port when it is read.
            _ = parsed.port
        except ValueError as e:
            raise invalid_endpoint from e
        self._parsed_endpoint = parsed
        self._staged_headers: Optional[Tuple[str, List[str]]] = None
        super().__init__()

    def _headers(self) -> Optional[Tuple[str, List[str]]]:
        """Returns the staged headers file and its header names, if any."""
        if not self.config.headers_file:
            return None
        if self._staged_headers is None:
            self._staged_headers = _stage_headers_file(
                self._headers_file_local_path())
        return self._staged_headers

    def _headers_file_local_path(self) -> str:
        assert self.config.headers_file is not None
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
        headers = self._headers()
        if headers is None:
            return setup
        # Export each header value from the delivered headers file so that
        # fluent-bit expands it from the environment at startup.
        exports = []
        for i in range(len(headers[1])):
            awk = (f'awk -v n={i + 1} {shlex.quote(_AWK_HEADER_VALUE)} '
                   f'{_REMOTE_HEADERS_PATH}')
            exports.append(f'export {_HEADER_ENV_PREFIX}{i}="$({awk})"')
        if not exports:
            return setup
        # The upload keeps the staged copy's 0600 mode; enforce it regardless,
        # since the file holds secrets.
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
            config['tls.verify'] = (
                'off' if self.config.tls.insecure_skip_verify else 'on')
        if self.config.protocol == _PROTOCOL_GRPC:
            config['grpc'] = 'on'
        if self.config.compression != 'none':
            config['compress'] = self.config.compression
        headers = [f'{k} {v}' for k, v in (self.config.headers or {}).items()]
        staged = self._headers()
        if staged is not None:
            for i, name in enumerate(staged[1]):
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
        # Called before provisioning, so a missing or malformed headers file
        # fails the launch before any instance is created.
        staged = self._headers()
        if staged is None:
            return {}
        return {_REMOTE_HEADERS_PATH: staged[0]}
