"""Stream a sandbox file directly to an S3-compatible OSS bucket."""

import base64
import os
from typing import Optional

import boto3
from botocore.config import Config
from botocore.utils import IterableToFileAdapter
from dotenv import load_dotenv

from agent_sandbox import Sandbox


def _require_env_var(name: str, fallback: Optional[str] = None) -> str:
    value = os.getenv(name, fallback)
    if not value:
        missing = f"{name}"
        if fallback:
            missing += f" (fallback {fallback})"
        raise RuntimeError(f"Missing required environment variable: {missing}")
    return value


def _build_s3_client():
    """Build a boto3 S3 client for OSS-compatible endpoints."""
    region = os.getenv("OSS_REGION") or os.getenv("AWS_REGION") or "us-east-1"
    endpoint = os.getenv("OSS_ENDPOINT")

    access_key = os.getenv("OSS_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("OSS_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")

    if not access_key or not secret_key:
        raise RuntimeError(
            "Missing credentials: set OSS_ACCESS_KEY_ID/OSS_SECRET_ACCESS_KEY or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY."
        )

    session = boto3.session.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )

    client_kwargs = {}
    if endpoint:
        client_kwargs["endpoint_url"] = endpoint

    # Transfer acceleration is disabled for OSS compatibility; enable v4 signing for most providers.
    client_kwargs["config"] = Config(signature_version="s3v4")

    return session.client("s3", **client_kwargs)


def main():
    """Write a PNG into the sandbox and stream it to OSS via the download iterator."""
    load_dotenv()

    sandbox_url = os.getenv("SANDBOX_BASE_URL", "http://localhost:8080")
    client = Sandbox(base_url=sandbox_url)

    bucket_name = _require_env_var("OSS_BUCKET")
    object_key = os.getenv("OSS_OBJECT_KEY", "uploads/test.png")

    home_dir = client.sandbox.get_context().home_dir
    png_path = f"{home_dir}/test.png"

    print(f"Sandbox home directory: {home_dir}")

    with open("./foo.png", "rb") as f:
        png_content = base64.b64encode(f.read()).decode("utf-8")

    png_write_result = client.file.write_file(
        file=png_path,
        content=png_content,
        encoding="base64",
    )
    print(f"✓ PNG written to sandbox at {png_path} ({png_write_result.data.bytes_written} bytes)")

    download_iterator = client.file.download_file(
        path=png_path,
        request_options={"chunk_size": 64 * 1024},
    )

    # Let botocore adapt the iterator to a readable stream without loading everything into memory.
    stream = IterableToFileAdapter(download_iterator)

    s3_client = _build_s3_client()
    print(f"Uploading to OSS bucket '{bucket_name}' as '{object_key}'...")

    s3_client.upload_fileobj(stream, bucket_name, object_key)

    print("✓ Upload complete.")


if __name__ == "__main__":
    main()
