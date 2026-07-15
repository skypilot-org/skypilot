"""GCP logging agent."""

import os
from typing import Any, Dict, Optional

import pydantic

from sky.clouds import gcp
from sky.logs.agent import FluentbitAgent
from sky.skylet import constants
from sky.utils import resources_utils

# Remote path where a user-provided service-account key (logs.gcp.
# credentials_file) is delivered. Fixed and home-relative so it is readable by
# the cluster's runtime user regardless of where the key lives on the API
# server (e.g. under another user's home, or behind a symlink).
_REMOTE_CREDENTIAL_PATH = os.path.join(constants.LOGGING_CONFIG_DIR,
                                       'gcp_credentials.json')


class _GCPLoggingConfig(pydantic.BaseModel):
    """Configuration for GCP logging agent."""
    project_id: Optional[str] = None
    credentials_file: Optional[str] = None
    additional_labels: Optional[Dict[str, str]] = None


class _StackdriverOutputConfig(pydantic.BaseModel):
    """Auxiliary model for building stackdriver output config in YAML.

    Ref: https://docs.fluentbit.io/manual/1.7/pipeline/outputs/stackdriver
    """
    name: str = 'stackdriver'
    match: str = '*'
    export_to_project_id: Optional[str] = None
    labels: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        config = self.model_dump(exclude_none=True)
        if self.labels:
            # Replace the label format from `{k: v}` to `k=v`
            label_str = ','.join([f'{k}={v}' for k, v in self.labels.items()])
            config['labels'] = label_str
        return config


class GCPLoggingAgent(FluentbitAgent):
    """GCP logging agent."""

    def __init__(self, config: Dict[str, Any]):
        self.config = _GCPLoggingConfig(**config)

    def get_setup_command(self,
                          cluster_name: resources_utils.ClusterName) -> str:
        credential_path = gcp.DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH
        if self.config.credentials_file:
            credential_path = _REMOTE_CREDENTIAL_PATH
        # Set GOOGLE_APPLICATION_CREDENTIALS and check whether credentials
        # is valid.
        # Stackdriver only support service account credentials or credentials
        # from metadata server (only available on GCE or GKE). If the default
        # credentials uploaded by API server is NOT a service account key and
        # there is NO metadata server available, the logging agent will fail to
        # authenticate and we require the user to upload a service account key
        # via logs.gcp.credentials_file in this case.
        # Note: fluent-bit v5.0.0 broke the stackdriver plugin's service
        # account auth entirely (both GOOGLE_APPLICATION_CREDENTIALS env var
        # and google_service_credentials config are ignored). We pin to v4.x
        # in the install command (see agent.py) and rely on the env var.
        # See: https://github.com/fluent/fluent-bit/issues/11619
        # TODO(aylei): check whether the credentials config is valid before
        # provision.
        pre_cmd = (f'export GOOGLE_APPLICATION_CREDENTIALS={credential_path}; '
                   f'cat {credential_path} | grep "service_account" || '
                   f'(echo "Credentials file {credential_path} is not a '
                   'service account key, check metadata server" && '
                   'curl -s http://metadata.google.internal >/dev/null || '
                   f'(echo "Neither service account key nor metadata server is '
                   'available. Set logs.gcp.credentials_file to a service '
                   'account key in server config and retry." && '
                   'exit 1;))')
        return pre_cmd + ' && ' + super().get_setup_command(cluster_name)

    def fluentbit_output_config(
            self, cluster_name: resources_utils.ClusterName) -> Dict[str, Any]:
        display_name = cluster_name.display_name
        unique_name = cluster_name.name_on_cloud

        return _StackdriverOutputConfig(
            export_to_project_id=self.config.project_id,
            labels={
                'skypilot_cluster_name': display_name,
                'skypilot_cluster_id': unique_name,
                **(self.config.additional_labels or {})
            },
        ).to_dict()

    def get_credential_file_mounts(self) -> Dict[str, str]:
        if not self.config.credentials_file:
            return {}
        # Resolve to a concrete local file before upload: expanduser handles
        # '~', and realpath resolves symlinks (e.g. a Kubernetes Secret mounted
        # as a volume, which exposes the key through a symlink chain) and
        # relative paths. Delivered to a fixed home-relative path so the
        # cluster's runtime user can read it even when the source lives under
        # another user's home (e.g. /root) on the API server.
        local_path = os.path.realpath(
            os.path.expanduser(self.config.credentials_file))
        return {_REMOTE_CREDENTIAL_PATH: local_path}
