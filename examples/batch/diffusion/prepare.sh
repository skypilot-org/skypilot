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
echo "[2/3] Generating prompts.jsonl (300 prompts)..."
PROMPTS_FILE="$(mktemp)"
python3 -c "
import json, random
subjects = [
    'astronaut', 'robot', 'dragon', 'cat', 'owl', 'whale', 'fox',
    'knight', 'wizard', 'mermaid', 'phoenix', 'wolf', 'deer', 'bear',
    'butterfly', 'eagle', 'dolphin', 'lion', 'panda', 'tiger',
]
scenes = [
    'riding a horse on mars', 'flying over a mountain', 'sitting in a garden',
    'walking through a forest', 'standing on a cliff at sunset',
    'swimming in the ocean', 'exploring a cave', 'resting by a campfire',
    'dancing in the rain', 'reading a book in a library',
    'playing in the snow', 'gazing at the stars', 'crossing a bridge',
    'sailing on a lake', 'climbing a tower',
]
styles = [
    'oil painting', 'digital art', 'watercolor', 'cyberpunk style',
    'fantasy art', 'ukiyo-e style', 'concept art', 'pixel art',
    'photorealistic', 'impressionist', 'art nouveau', 'pop art',
    'studio ghibli style', 'low poly 3D', 'pencil sketch',
]
random.seed(42)
for i in range(10):
    s = random.choice(subjects)
    sc = random.choice(scenes)
    st = random.choice(styles)
    print(json.dumps({'prompt': f'a {s} {sc}, {st}'}))
" > "${PROMPTS_FILE}"
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
