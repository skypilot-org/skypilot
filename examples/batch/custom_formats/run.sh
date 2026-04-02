#!/usr/bin/env bash
# End-to-end run script for the custom format batch example.
# Run from project root:
#   bash examples/batch/custom_formats/run.sh
set -euo pipefail

BUCKET="${SKY_BATCH_BUCKET:-sky-batch-custom-fmt-${USER:-default}}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"

echo "=== Sky Batch — Custom Format Example ==="
echo "Bucket: s3://${BUCKET}"
echo

# ---- 1. Create S3 bucket (for output only — no input file needed) ----------
echo "[1/3] Creating S3 bucket..."
if aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
    echo "  Bucket already exists, reusing."
else
    if [ "${REGION}" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}"
    else
        aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}" \
            --create-bucket-configuration LocationConstraint="${REGION}"
    fi
    echo "  Created s3://${BUCKET}"
fi

# ---- 2. Clean up previous output -------------------------------------------
echo "[2/3] Cleaning up previous output..."
aws s3 rm "s3://${BUCKET}/output/" --recursive 2>/dev/null && \
    echo "  Removed previous output." || echo "  No previous output to clean."
aws s3 rm "s3://${BUCKET}/.sky_batch_tmp/" --recursive 2>/dev/null && \
    echo "  Removed temp files." || true

# ---- 3. Run the batch job --------------------------------------------------
echo "[3/3] Running batch job..."
echo
export SKY_BATCH_BUCKET="${BUCKET}"
python examples/batch/custom_formats/process_range.py

echo
echo "Listing output files:"
echo "--- texts/ ---"
aws s3 ls "s3://${BUCKET}/output/texts/" 2>/dev/null || echo "  (none)"
echo "--- metadata.yaml ---"
aws s3 ls "s3://${BUCKET}/output/metadata.yaml" 2>/dev/null || echo "  (none)"
