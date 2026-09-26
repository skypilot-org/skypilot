class R2CloudStorage(CloudStorage):
    """Cloudflare Cloud Storage."""

    # Complete fixed command list to safely resolve the awscli path
    _GET_AWSCLI = [
        ('if ! aws --version >/dev/null 2>&1; then '
         f'{constants.SKY_UV_PIP_CMD} install awscli; '
         'fi; '
         'awscli_path=$(which aws || echo '
         f'{constants.SKY_REMOTE_PYTHON_ENV}/bin/aws)'),
    ]

    def __init__(self, name: str, source: str, mode: StorageMode = StorageMode.MOUNT):
        super().__init__(name, source, mode)
        self.endpoint_url = "https://cloudflarestorage.com"
        self.credentials_path = "~/.aws/credentials"

    def make_sync_command(self, source: str, target: str) -> str:
        """Generates the bash string to sync data using the resolved path variable."""
        return (
            f"export AWS_SHARED_CREDENTIALS_FILE={self.credentials_path} && "
            f"$awscli_path s3 sync {source} {target} --endpoint-url={self.endpoint_url}"
        )
