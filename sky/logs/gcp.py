"""GCP log store module."""
import datetime
import typing
from typing import List

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common
from sky.logs import logs

logger = sky_logging.init_logger(__name__)

# TODO(aylei): Maybe we need to separate the gcp and gcp cloud logging
# dependencies.
_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for GCP Cloud logging. '
                         'Try pip install "skypilot[gcp]"')

if typing.TYPE_CHECKING:
    from google.cloud import logging
else:
    logging = common.LazyImport('google.cloud.logging',
                                import_error_message=_IMPORT_ERROR_MESSAGE)

# GCP logging automatically indexes the labels.* fields of an LogEntry,
# refer to https://cloud.google.com/logging/docs/analyze/custom-index
_CLUSTER_HASH_LABEL_KEY = 'labels.cluster_hash'
_CLUSTER_NAME_LABEL_KEY = 'labels.cluster_name'
_JOB_NAME_LABEL_KEY = 'labels.job_name'
_JOB_ID_LABEL_KEY = 'labels.job_id'
_MANAGED_JOB_ID_LABEL_KEY = 'labels.managed_job_id'

class GCPLogStore(logs.LogStore):
    """GCP log store."""

    def __init__(self, project: str):
        self.project = project
        try:
            self.client = logging.Client(project=project)
            self.logger = self.client.logger('skypilot')
            self.logger.log_empty()
        except Exception as e: # pylint: disable=broad-except
            raise exceptions.LogStoreError(
                f'Failed to initialize GCP log store: {e}')

    def write(self, lines: List[str], labels: logs.LogLabels) -> None:
        if not lines:
            return
        gcp_labels = {
            _CLUSTER_HASH_LABEL_KEY: labels.cluster_hash,
            _CLUSTER_NAME_LABEL_KEY: labels.cluster_name,
            _JOB_NAME_LABEL_KEY: labels.job_name,
            _JOB_ID_LABEL_KEY: labels.job_id,
            _MANAGED_JOB_ID_LABEL_KEY: labels.managed_job_id,
        }
        try:
            with self.logger.batch() as batch:
                for line in lines:
                    batch.log_text(line,
                                   labels=gcp_labels,
                                   timestamp=datetime.datetime.now())
                batch.commit()
        except Exception as e: # pylint: disable=broad-except
            raise exceptions.LogStoreError(
                f'Failed to write logs to GCP log store: {e}')
