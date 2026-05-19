#!/usr/bin/env bash
# End-to-end run script for the diffusion batch example.
# Run from project root:
#   bash examples/batch/diffusion/run.sh
set -euo pipefail

BUCKET="${SKY_BATCH_BUCKET:-sky-batch-diffusion-${USER:-default}}"

echo "=== Sky Batch — Diffusion Example ==="
echo "Bucket: s3://${BUCKET}"
echo

# ---- 1. Prepare bucket and prompts --------------------------------------
echo "[1/2] Preparing S3 bucket and prompts..."
bash examples/batch/diffusion/prepare.sh "${BUCKET}"

# ---- 2. Clean up previous output ----------------------------------------
echo "[2/3] Cleaning up previous output..."
aws s3 rm "s3://${BUCKET}/generated_images/" --recursive 2>/dev/null && \
    echo "  Removed s3://${BUCKET}/generated_images/" || \
    echo "  No previous output to clean."
aws s3 rm "s3://${BUCKET}/manifest.jsonl" 2>/dev/null && \
    echo "  Removed s3://${BUCKET}/manifest.jsonl" || \
    echo "  No previous manifest to clean."

# ---- 3. Run the batch job -----------------------------------------------
echo "[3/3] Running batch job..."
echo
export SKY_BATCH_BUCKET="${BUCKET}"
python examples/batch/diffusion/generate_images.py
