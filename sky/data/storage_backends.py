"""Cloud storage backends used by SkyPilot data transfers."""

from sky.cloud_stores import CloudStorage
from sky.data.storage import StorageMode
from sky.skylet import constants


class R2CloudStorage(CloudStorage):
    """Cloudflare Cloud Storage."""

    _GET_AWSCLI = [
        ('if ! aws --version >/dev/null 2>&1; then '
         f'{constants.SKY_UV_PIP_CMD} install awscli; '
         'fi; '
         'awscli_path=$(which aws || echo '
         f'{constants.SKY_REMOTE_PYTHON_ENV}/bin/aws)'),
    ]

    def __init__(self,
                 name: str,
                 source: str,
                 mode: StorageMode = StorageMode.MOUNT):
        super().__init__()
        self.name = name
        self.source = source
        self.mode = mode
        self.endpoint_url = 'https://cloudflarestorage.com'
        self.credentials_path = '~/.aws/credentials'

    def make_sync_command(self, source: str, target: str) -> str:
        """Generate the command to sync data using the resolved AWS CLI."""
        sync_command = (
            f'export AWS_SHARED_CREDENTIALS_FILE={self.credentials_path} && '
            f'$awscli_path s3 sync {source} {target} '
            f'--endpoint-url={self.endpoint_url}')
        return ' && '.join([*self._GET_AWSCLI, sync_command])
