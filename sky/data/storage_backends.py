from sky.data.storage import CloudStorage, StorageMode
from sky.server import constants


class R2CloudStorage(CloudStorage):  # type: ignore
    """Cloudflare Cloud Storage."""

    # Consolidated installation and path resolution into a single robust bash command
    _GET_AWSCLI = [
        f'if ! aws --version >/dev/null 2>&1; then {constants.SKY_UV_PIP_CMD} install awscli; fi; ',  # type: ignore
        f'awscli_path=$(which aws || echo {constants.SKY_REMOTE_PYTHON_ENV}/bin/aws)',  # type: ignore
    ]

    def __init__(self, name: str, source: str, mode: StorageMode = StorageMode.MOUNT):
        """Initialize R2CloudStorage."""
        super().__init__(name, source, mode)
        self.endpoint_url = "https://cloudflarestorage.com"
        self.credentials_path = "~/.aws/credentials"

    def make_sync_command(self, source: str, target: str) -> str:
        """Generates the bash string to sync data using the resolved path variable."""
        return (
            f"export AWS_SHARED_CREDENTIALS_FILE={self.credentials_path} && "
            f"$awscli_path s3 sync {source} {target} --endpoint-url={self.endpoint_url}"
        )
