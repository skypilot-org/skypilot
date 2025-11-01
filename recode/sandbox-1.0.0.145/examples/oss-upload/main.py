"""Stream sandbox file data directly into Volcano Engine TOS using the official SDK."""

import base64
import os
from typing import Optional

import tos
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


def main():
    """Write `test.png` into the sandbox and stream it to TOS via `put_object`."""
    load_dotenv()

    sandbox_url = os.getenv("SANDBOX_BASE_URL", "http://localhost:8080")
    client = Sandbox(base_url=sandbox_url)

    bucket_name = _require_env_var("TOS_BUCKET")
    object_key = os.getenv("TOS_OBJECT_KEY", "uploads/test.png")

    ak = _require_env_var("TOS_ACCESS_KEY")
    sk = _require_env_var("TOS_SECRET_KEY")
    endpoint = _require_env_var("TOS_ENDPOINT")
    region = _require_env_var("TOS_REGION")

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

    tos_client = tos.TosClientV2(ak, sk, endpoint, region)

    print(f"Uploading to TOS bucket '{bucket_name}' as '{object_key}'...")

    try:
        result = tos_client.put_object(
            bucket_name,
            object_key,
            content=download_iterator,
        )
    except tos.exceptions.TosClientError as err:
        print(f"✗ Upload failed with client error: message={err.message}, cause={err.cause}")
        return
    except tos.exceptions.TosServerError as err:
        print(
            "✗ Upload failed with server error: "
            f"code={err.code}, request_id={err.request_id}, http_status={err.status_code}"
        )
        print(f"  message={err.message}, ec={err.ec}, url={err.request_url}")
        return
    except Exception as err:
        print(f"✗ Upload failed with unexpected error: {err}")
        return

    print(
        "✓ Upload complete: "
        f"status_code={result.status_code}, request_id={result.request_id}, crc64={result.hash_crc64_ecma}"
    )


if __name__ == "__main__":
    main()
