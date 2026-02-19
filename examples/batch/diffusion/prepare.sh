#!/usr/bin/env bash
# Prepare S3 bucket and prompts for the diffusion batch example.
#
# Usage (from project root):
#   bash examples/batch/diffusion/prepare.sh                    # auto-generate bucket name
#   bash examples/batch/diffusion/prepare.sh my-bucket-name     # use a specific bucket
#
# After running, follow the printed instructions to launch the job.
set -euo pipefail

BUCKET="${1:-sky-batch-diffusion-$(date +%Y%m%d)-$(openssl rand -hex 4)}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"

echo "=== Sky Batch Diffusion Example Setup ==="
echo "Bucket:  s3://${BUCKET}"
echo "Region:  ${REGION}"
echo

# ---- 1. Create S3 bucket ------------------------------------------------
echo "[1/3] Creating S3 bucket..."
if aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
    echo "  Bucket s3://${BUCKET} already exists, reusing."
else
    if [ "${REGION}" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}"
    else
        aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}" \
            --create-bucket-configuration LocationConstraint="${REGION}"
    fi
    echo "  Created s3://${BUCKET}"
fi

# ---- 2. Generate prompts.jsonl ------------------------------------------
echo "[2/3] Generating prompts.jsonl..."
PROMPTS_FILE="$(mktemp)"
cat > "${PROMPTS_FILE}" << 'EOF'
{"prompt": "a photo of an astronaut riding a horse on mars"}
{"prompt": "a beautiful sunset over the ocean, oil painting"}
{"prompt": "a corgi wearing sunglasses at the beach, digital art"}
{"prompt": "a futuristic city skyline at night, cyberpunk style"}
{"prompt": "a cozy cabin in a snowy forest, watercolor"}
{"prompt": "a dragon flying over a medieval castle, fantasy art"}
{"prompt": "a steampunk mechanical owl perched on old books"}
{"prompt": "a Japanese zen garden in autumn, ukiyo-e style"}
{"prompt": "an underwater city with bioluminescent coral, concept art"}
EOF
NUM_PROMPTS=$(wc -l < "${PROMPTS_FILE}" | tr -d ' ')
echo "  Generated ${NUM_PROMPTS} prompts."

# ---- 3. Upload to S3 ----------------------------------------------------
echo "[3/3] Uploading prompts.jsonl to S3..."
aws s3 cp "${PROMPTS_FILE}" "s3://${BUCKET}/prompts.jsonl"
rm -f "${PROMPTS_FILE}"
echo "  Uploaded to s3://${BUCKET}/prompts.jsonl"

# ---- Done ----------------------------------------------------------------
echo
echo "=== Setup complete ==="
echo
echo "Run the batch job (from project root):"
echo
echo "  export SKY_BATCH_BUCKET=${BUCKET}"
echo "  python examples/batch/diffusion/generate_images.py"
echo
echo "After completion, download results:"
echo
echo "  aws s3 cp s3://${BUCKET}/generated_images/ ./generated_images/ --recursive"
echo
