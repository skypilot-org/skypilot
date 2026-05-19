"""Smoke tests for batch processing examples (examples/batch/).

Tests cover:
- Simple batch (text doubling): data setup, execution, result verification,
  job queue format, pool column.
- Diffusion batch (image generation): GPU workers, multi-output (images +
  manifest JSONL).
- Batch cancel: cancel a running batch job mid-flight.
- Batch HA: kill controller mid-flight, verify resume from DB.
"""
import tempfile

import pytest
from smoke_tests import smoke_tests_utils

from sky import skypilot_config


def _storage_cmds(generic_cloud: str, bucket: str):
    """Return cloud-specific storage command fragments for batch tests.

    Returns:
        (url, create_cmd, delete_cmd, cp, rm, ls, rm_r) where url is the
        bucket URL, create/delete_cmd are full shell commands, cp/rm/ls are
        command prefixes, and rm_r(path) returns a recursive-remove command.
    """
    if generic_cloud == 'gcp':
        url = f'gs://{bucket}'
        return (url, f'gsutil mb {url}', f'gsutil rm -r {url}', 'gsutil cp',
                'gsutil rm', 'gsutil ls', lambda p: f'gsutil rm -r {p}')
    # Default to AWS
    url = f's3://{bucket}'
    return (url,
            f'aws s3api create-bucket --bucket {bucket} --region us-east-1',
            f'aws s3 rb {url} --force', 'aws s3 cp', 'aws s3 rm', 'aws s3 ls',
            lambda p: f'aws s3 rm {p} --recursive')


# ---------- Test simple batch (text doubling) ----------
@pytest.mark.batch
def test_batch_simple(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    bucket = f'sky-batch-smpl-{name}'
    pool_name = f'batch-smpl-pool-{name}'
    url, create_bkt, delete_bkt, cp, rm, _, rm_r = _storage_cmds(
        generic_cloud, bucket)
    store = 'gs' if generic_cloud == 'gcp' else 's3'

    test = smoke_tests_utils.Test(
        'batch_simple',
        [
            # --- Pre-cleanup: remove stale pool from previous runs ---
            f'sky jobs pool down {pool_name} -y 2>/dev/null || true',
            f'sky serve down {pool_name} -y 2>/dev/null || true',
            # --- Create pool with generic_cloud ---
            (f's=$(sky jobs pool apply -p {pool_name} --infra {generic_cloud}'
             f' examples/batch/simple/pool.yaml -y); '
             f'echo "$s"; '
             f'echo "$s" | grep "Successfully created pool"'),
            # --- Data setup (extracted from examples/batch/simple/run.sh) ---
            create_bkt,
            # Generate test data: 50 items (batch_size 2 -> 25 batches)
            (f'for i in $(seq 1 50); do '
             f'echo "{{\\"text\\": \\"word_$i\\"}}"; '
             f'done > /tmp/batch-input-{name}.jsonl'),
            f'{cp} /tmp/batch-input-{name}.jsonl {url}/test.jsonl',
            # Clean previous output
            f'{rm} {url}/output.jsonl 2>/dev/null || true',
            f'{rm_r(f"{url}/.sky_batch_tmp/")} 2>/dev/null || true',

            # --- Run batch job (pool already created, blocks until done) ---
            f'python examples/batch/simple/double_text.py',

            # --- Verify job queue format (header columns) ---
            # Expected header: ID TASK NAME ... STATUS PROGRESS POOL
            (f"sky jobs queue | "
             f"grep 'STATUS' | grep 'PROGRESS' | grep 'POOL'"),

            # --- Verify final job status (filter to our pool) ---
            f'sky jobs queue | grep "{pool_name}" | grep SUCCEEDED',

            # --- Verify output results ---
            f'{cp} {url}/output.jsonl /tmp/batch-output-{name}.jsonl',
            # Result count must match input count
            f'test $(wc -l < /tmp/batch-output-{name}.jsonl) -eq 50',
            # Check doubled text at boundaries and middle
            f'grep "word_1word_1" /tmp/batch-output-{name}.jsonl',
            f'grep "word_25word_25" /tmp/batch-output-{name}.jsonl',
            f'grep "word_50word_50" /tmp/batch-output-{name}.jsonl',
            # Every line must contain both "text" and "output" keys
            (f'test $(grep -c \'"text":\' /tmp/batch-output-{name}.jsonl) '
             f'-eq 50'),
            (f'test $(grep -c \'"output":\' /tmp/batch-output-{name}.jsonl) '
             f'-eq 50'),
            # Validate JSON structure: every line is parseable and has the
            # expected keys with correctly doubled values
            (f"python3 << 'PYEOF'\n"
             "import json, sys\n"
             f"path = '/tmp/batch-output-{name}.jsonl'\n"
             "results = [json.loads(l) for l in open(path)]\n"
             "assert len(results) == 50, f'Expected 50, got {len(results)}'\n"
             "texts = set()\n"
             "for r in results:\n"
             "    assert 'text' in r, f'Missing text key: {r}'\n"
             "    assert 'output' in r, f'Missing output key: {r}'\n"
             "    assert r['output'] == r['text'] * 2, (\n"
             "        f'output != text*2: {r}')\n"
             "    texts.add(r['text'])\n"
             "expected = set(f'word_{i}' for i in range(1, 51))\n"
             "assert texts == expected, (\n"
             "    f'Missing: {expected - texts}, Extra: {texts - expected}')\n"
             "print(f'All {len(results)} results valid')\n"
             "PYEOF"),
        ],
        # Teardown: remove pool, bucket, and temp files
        (f'sky jobs pool down {pool_name} -y;'
         f' sky serve down {pool_name} -y 2>/dev/null || true;'
         f' {delete_bkt};'
         f' rm -f /tmp/batch-input-{name}.jsonl'
         f' /tmp/batch-output-{name}.jsonl'),
        timeout=30 * 60,
        env={
            'SKY_BATCH_BUCKET': bucket,
            'SKY_BATCH_STORE': store,
            'BATCH_POOL_NAME': pool_name,
        },
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Test diffusion batch (image generation) ----------
@pytest.mark.batch
@pytest.mark.resource_heavy
@pytest.mark.no_kubernetes  # pool.yaml hardcodes L4 GPU; K8s CI clusters may not have it
def test_batch_diffusion(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    bucket = f'sky-batch-diff-{name}'
    pool_name = 'diffusion-pool'
    url, create_bkt, delete_bkt, cp, rm, ls, rm_r = _storage_cmds(
        generic_cloud, bucket)
    store = 'gs' if generic_cloud == 'gcp' else 's3'

    test = smoke_tests_utils.Test(
        'batch_diffusion',
        [
            # --- Pre-cleanup: remove stale pool from previous runs ---
            f'sky jobs pool down {pool_name} -y 2>/dev/null || true',
            f'sky serve down {pool_name} -y 2>/dev/null || true',
            # --- Create GPU pool with generic_cloud ---
            (f's=$(sky jobs pool apply -p {pool_name} --infra {generic_cloud}'
             f' examples/batch/diffusion/pool.yaml -y); '
             f'echo "$s"; '
             f'echo "$s" | grep "Successfully created pool"'),
            # --- Data setup (extracted from examples/batch/diffusion/) ---
            create_bkt,
            # Upload 3 prompts
            (f"cat > /tmp/batch-prompts-{name}.jsonl << 'EOF'\n"
             '{"prompt": "a cat sitting in a garden, digital art"}\n'
             '{"prompt": "a robot flying over a mountain, watercolor"}\n'
             '{"prompt": "an owl reading a book, oil painting"}\n'
             'EOF'),
            f'{cp} /tmp/batch-prompts-{name}.jsonl {url}/prompts.jsonl',
            # Clean previous output
            f'{rm_r(f"{url}/generated_images/")} 2>/dev/null || true',
            f'{rm} {url}/manifest.jsonl 2>/dev/null || true',

            # --- Run batch job (pool already created, blocks until done) ---
            f'python examples/batch/diffusion/generate_images.py',

            # --- Verify job queue ---
            f'sky jobs queue | grep "{pool_name}" | grep SUCCEEDED',

            # --- Verify manifest content ---
            f'{cp} {url}/manifest.jsonl /tmp/batch-manifest-{name}.jsonl',
            # Each prompt appears in the manifest
            f'grep "cat sitting" /tmp/batch-manifest-{name}.jsonl',
            f'grep "robot flying" /tmp/batch-manifest-{name}.jsonl',
            f'grep "owl reading" /tmp/batch-manifest-{name}.jsonl',
            # Manifest has exactly 3 entries (one per prompt)
            f'test $(wc -l < /tmp/batch-manifest-{name}.jsonl) -eq 3',
            # Validate manifest JSON: each line must have a "prompt" key
            (f"python3 << 'PYEOF'\n"
             "import json\n"
             f"path = '/tmp/batch-manifest-{name}.jsonl'\n"
             "entries = [json.loads(l) for l in open(path)]\n"
             "assert len(entries) == 3, f'Expected 3, got {len(entries)}'\n"
             "for e in entries:\n"
             "    assert 'prompt' in e, f'Missing prompt key: {e}'\n"
             "print(f'Manifest valid: {len(entries)} entries')\n"
             "PYEOF"),

            # --- Verify generated images ---
            f'{ls} {url}/generated_images/ | grep ".png"',
            # Exactly 3 images (one per prompt)
            (f'test $({ls} {url}/generated_images/ '
             f'| grep -c ".png") -eq 3'),
            # Check zero-padded naming: 00000000.png, 00000001.png, 00000002.png
            f'{ls} {url}/generated_images/ | grep "00000000.png"',
            f'{ls} {url}/generated_images/ | grep "00000002.png"',
        ],
        # Teardown: remove pool, bucket, and temp files
        (f'sky jobs pool down {pool_name} -y;'
         f' sky serve down {pool_name} -y 2>/dev/null || true;'
         f' {delete_bkt};'
         f' rm -f /tmp/batch-prompts-{name}.jsonl'
         f' /tmp/batch-manifest-{name}.jsonl'),
        timeout=45 * 60,
        env={
            'SKY_BATCH_BUCKET': bucket,
            'SKY_BATCH_STORE': store
        },
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Test custom formats (range input, text + JSON file output) --------
@pytest.mark.batch
def test_batch_custom_formats(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    bucket = f'sky-batch-cfmt-{name}'
    pool_name = 'custom-fmt-pool'
    url, create_bkt, delete_bkt, cp, _, ls, rm_r = _storage_cmds(
        generic_cloud, bucket)
    store = 'gs' if generic_cloud == 'gcp' else 's3'

    test = smoke_tests_utils.Test(
        'batch_custom_formats',
        [
            # --- Pre-cleanup: remove stale pool from previous runs ---
            f'sky jobs pool down {pool_name} -y 2>/dev/null || true',
            f'sky serve down {pool_name} -y 2>/dev/null || true',
            # --- Create pool with generic_cloud ---
            (f's=$(sky jobs pool apply -p {pool_name} --infra {generic_cloud}'
             f' examples/batch/custom_formats/pool.yaml -y); '
             f'echo "$s"; '
             f'echo "$s" | grep "Successfully created pool"'),
            # --- Create bucket (output only — no input data needed) ---
            create_bkt,
            # Clean previous output
            f'{rm_r(f"{url}/output/")} 2>/dev/null || true',
            f'{rm_r(f"{url}/.sky_batch_tmp/")} 2>/dev/null || true',

            # --- Run batch job (RangeReader, TextWriter, YamlWriter) ---
            f'python examples/batch/custom_formats/process_range.py',

            # --- Verify final job status ---
            f'sky jobs queue | grep "{pool_name}" | grep SUCCEEDED',

            # --- Verify text output files ---
            # 20 items -> 20 .txt files
            (f'test $({ls} {url}/output/texts/ '
             f'| grep -c "\\.txt") -eq 20'),
            # Check zero-padded naming
            f'{ls} {url}/output/texts/ | grep "00000000.txt"',
            f'{ls} {url}/output/texts/ | grep "00000019.txt"',
            # Spot-check content of first text file
            (f'{cp} {url}/output/texts/00000000.txt - '
             f'| grep "Item 0"'),

            # --- Verify merged YAML metadata file ---
            # Single .yaml file with all 20 items
            f'{ls} {url}/output/metadata.yaml',
            # Validate YAML: parseable, correct count, expected keys
            (f"python3 << 'PYEOF'\n"
             "import subprocess\n"
             "import yaml\n"
             "data = subprocess.check_output(\n"
             f"    '{cp} {url}/output/metadata.yaml -', shell=True)\n"
             "items = yaml.safe_load(data)\n"
             "assert len(items) == 20, f'Expected 20 items, got {len(items)}'\n"
             "for item in items:\n"
             "    meta = item['metadata']\n"
             "    assert 'id' in meta and 'squared' in meta and 'tag' in meta, (\n"
             "        f'Missing keys: {meta}')\n"
             "    assert meta['squared'] == meta['id'] ** 2, f'Wrong squared: {meta}'\n"
             "    assert meta['tag'] in ('alpha', 'beta', 'gamma'), (\n"
             "        f'Invalid tag: {meta}')\n"
             "print('YAML metadata file valid')\n"
             "PYEOF"),
        ],
        # Teardown: remove pool, bucket, and temp files
        (f'sky jobs pool down {pool_name} -y;'
         f' sky serve down {pool_name} -y 2>/dev/null || true;'
         f' {delete_bkt}'),
        timeout=30 * 60,
        env={
            'SKY_BATCH_BUCKET': bucket,
            'SKY_BATCH_STORE': store
        },
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Test batch cancel ----------
@pytest.mark.batch
def test_batch_cancel(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    bucket = f'sky-batch-cncl-{name}'
    pool_name = f'batch-cncl-pool-{name}'
    url, create_bkt, delete_bkt, cp, rm, _, rm_r = _storage_cmds(
        generic_cloud, bucket)
    store = 'gs' if generic_cloud == 'gcp' else 's3'

    test = smoke_tests_utils.Test(
        'batch_cancel',
        [
            # --- Pre-cleanup: remove stale pool from previous runs ---
            f'sky jobs pool down {pool_name} -y 2>/dev/null || true',
            f'sky serve down {pool_name} -y 2>/dev/null || true',
            # --- Create pool with generic_cloud ---
            (f's=$(sky jobs pool apply -p {pool_name} --infra {generic_cloud}'
             f' examples/batch/simple/pool.yaml -y); '
             f'echo "$s"; '
             f'echo "$s" | grep "Successfully created pool"'),
            # --- Setup with large dataset so the job runs long enough ---
            create_bkt,
            # 500 items / batch_size 2 = 250 batches -> plenty of time to cancel
            (f'for i in $(seq 0 499); do '
             f'echo "{{\\"text\\": \\"item_$i\\"}}"; '
             f'done > /tmp/batch-cancel-input-{name}.jsonl'),
            f'{cp} /tmp/batch-cancel-input-{name}.jsonl {url}/test.jsonl',
            f'{rm} {url}/output.jsonl 2>/dev/null || true',
            f'{rm_r(f"{url}/.sky_batch_tmp/")} 2>/dev/null || true',

            # --- Launch in background, wait for RUNNING, then cancel ---
            (f"python examples/batch/simple/double_text.py &\n"
             f"BGPID=$!\n"
             f"echo \"Started batch job for cancel test (PID=$BGPID)\"\n"
             f"JOB_ID=''\n"
             f"for i in $(seq 1 90); do\n"
             f"  JOB_ID=$(sky jobs queue 2>/dev/null | "
             f"awk '$1 ~ /^[0-9]+$/ && /RUNNING/ {{print $1; exit}}')\n"
             f"  if [ -n \"$JOB_ID\" ]; then break; fi\n"
             f"  sleep 5\n"
             f"done\n"
             f"echo \"Found batch job ID=$JOB_ID, cancelling...\"\n"
             f"sky jobs cancel $JOB_ID -y\n"
             f"echo \"Cancel requested, waiting for background process...\"\n"
             f"wait $BGPID 2>/dev/null || true"),

            # --- Verify cancellation status (scoped to our pool) ---
            (f'sky jobs queue | grep "{pool_name}" '
             f'| grep -E "CANCELLING|CANCELLED"'),
        ],
        # Teardown: remove pool, bucket, and temp files
        (f'sky jobs pool down {pool_name} -y;'
         f' sky serve down {pool_name} -y 2>/dev/null || true;'
         f' {delete_bkt};'
         f' rm -f /tmp/batch-cancel-input-{name}.jsonl'),
        timeout=30 * 60,
        env={
            'SKY_BATCH_BUCKET': bucket,
            'SKY_BATCH_STORE': store,
            'BATCH_POOL_NAME': pool_name,
        },
    )
    smoke_tests_utils.run_one_test(test)
