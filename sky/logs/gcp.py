"""GCP logging agent."""

from typing import Any, Dict, Optional

import pydantic

from sky.clouds import gcp
from sky.logs.agent import FluentbitAgent
from sky.utils import resources_utils


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
            credential_path = self.config.credentials_file
        # Set GOOGLE_APPLICATION_CREDENTIALS and check whether credentials
        # is valid.
        # Stackdriver only support service account credentials or credentials
        # from metadata server (only available on GCE or GKE). If the default
        # credentials uploaded by API server is NOT a service account key and
        # there is NO metadata server available, the logging agent will fail to
        # authenticate and we require the user to upload a service account key
        # via logs.gcp.credentials_file in this case.
        # Also note that we use env var instead of YAML config to specify the
        # service account key file path in order to resolve the home directory
        # more reliably.
        # Ref: https://github.com/fluent/fluent-bit/issues/8804
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
        if self.config.credentials_file:
            return {self.config.credentials_file: self.config.credentials_file}
        return {}
