#!/usr/bin/env bash
# End-to-end run script for the simple batch example.
# Run from project root:
#   bash examples/batch/simple/run.sh
set -euo pipefail

BUCKET="${SKY_BATCH_BUCKET:-sky-batch-simple-${USER:-default}}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"

echo "=== Sky Batch — Simple Example ==="
echo "Bucket: s3://${BUCKET}"
echo

# ---- 1. Create S3 bucket ------------------------------------------------
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

# ---- 2. Generate and upload test data -----------------------------------
echo "[2/3] Uploading test.jsonl..."
TMPFILE="$(mktemp)"
cat > "${TMPFILE}" << 'EOF'
{"text": "hello"}
{"text": "world"}
{"text": "sky"}
{"text": "batch"}
EOF
aws s3 cp "${TMPFILE}" "s3://${BUCKET}/test.jsonl"
rm -f "${TMPFILE}"
echo "  Uploaded 4 items to s3://${BUCKET}/test.jsonl"

# ---- 3. Clean up previous output ----------------------------------------
echo "[3/4] Cleaning up previous output..."
aws s3 rm "s3://${BUCKET}/output.jsonl" 2>/dev/null && \
    echo "  Removed s3://${BUCKET}/output.jsonl" || \
    echo "  No previous output to clean."
aws s3 rm "s3://${BUCKET}/.sky_batch_tmp/" --recursive 2>/dev/null && \
    echo "  Removed temp files." || true

# ---- 4. Run the batch job -----------------------------------------------
echo "[4/4] Running batch job..."
echo
export SKY_BATCH_BUCKET="${BUCKET}"
python examples/batch/simple/double_text.py
