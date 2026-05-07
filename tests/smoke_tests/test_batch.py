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

# 1. TODO(lloyd): Marking the batch tests below as no_remote_server because
# they all run `sky jobs pool apply`, and on shared API server environments
# (e.g. the shared GKE pipeline) the controller has limited memory which
# caps concurrent services at 1. Multiple Buildkite jobs in parallel race
# for that single slot and fail with "Max number of services reached: 1/1".
# Remove this when consolidation mode is enabled by default or we have an
# option to not allow shared env tests.


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
@pytest.mark.no_remote_server  # see note 1 above
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
@pytest.mark.no_remote_server  # see note 1 above
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
@pytest.mark.no_remote_server  # see note 1 above
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
@pytest.mark.no_remote_server  # see note 1 above
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


# ---------- Test batch resume from cancelled job ----------
@pytest.mark.batch
@pytest.mark.no_remote_server  # see note 1 above
def test_batch_resume(generic_cloud: str):
    """Cancel a batch job mid-flight, then resume from its job ID.

    Verifies that:
    1. Completed batches from the original run are skipped.
    2. Failed/un-run batches are re-dispatched.
    3. Final output contains all items.
    4. The resumed job's metadata contains ``batch_resume_from``.
    """
    name = smoke_tests_utils.get_cluster_name()
    bucket = f'sky-batch-rsm-{name}'
    pool_name = f'batch-rsm-pool-{name}'
    url, create_bkt, delete_bkt, cp, rm, _, rm_r = _storage_cmds(
        generic_cloud, bucket)
    store = 'gs' if generic_cloud == 'gcp' else 's3'

    # Launcher script file; accepts resume_from via RESUME_FROM env var.
    script = f'/tmp/batch-resume-launcher-{name}.py'
    run_launcher = f'python {script}'

    test = smoke_tests_utils.Test(
        'batch_resume',
        [
            # --- Write launcher script to file (inspect.getsource needs it) ---
            (f"cat > {script} << 'PYEOF'\n"
             "import os, sky\n"
             f"bucket = '{bucket}'\n"
             f"store = '{store}'\n"
             f"pool_name = '{pool_name}'\n"
             "input_path = f'{store}://{bucket}/test.jsonl'\n"
             "output_path = f'{store}://{bucket}/output.jsonl'\n"
             "\n"
             "@sky.batch.remote_function\n"
             "def double_text():\n"
             "    for batch in sky.batch.load():\n"
             "        results = [{'text': item.get('text', ''),\n"
             "                     'output': item.get('text', '') * 2}\n"
             "                    for item in batch]\n"
             "        sky.batch.save_results(results)\n"
             "\n"
             "resume_from = os.environ.get('RESUME_FROM')\n"
             "resume_from = int(resume_from) if resume_from else None\n"
             "ds = sky.batch.Dataset(sky.batch.JsonReader(input_path))\n"
             "ds.map(\n"
             "    double_text,\n"
             "    pool_name=pool_name,\n"
             "    batch_size=2,\n"
             "    output=sky.batch.JsonWriter(output_path),\n"
             "    resume_from=resume_from,\n"
             ")\n"
             "PYEOF"),
            # --- Pre-cleanup ---
            f'sky jobs pool down {pool_name} -y 2>/dev/null || true',
            f'sky serve down {pool_name} -y 2>/dev/null || true',
            # --- Create pool ---
            (f's=$(sky jobs pool apply -p {pool_name} --infra {generic_cloud}'
             f' examples/batch/simple/pool.yaml -y); '
             f'echo "$s"; '
             f'echo "$s" | grep "Successfully created pool"'),
            # --- Data setup: 40 items / batch_size 2 = 20 batches ---
            create_bkt,
            (f'for i in $(seq 0 39); do '
             f'echo "{{\\"text\\": \\"item_$i\\"}}"; '
             f'done > /tmp/batch-resume-input-{name}.jsonl'),
            f'{cp} /tmp/batch-resume-input-{name}.jsonl {url}/test.jsonl',
            f'{rm} {url}/output.jsonl 2>/dev/null || true',
            f'{rm_r(f"{url}/.sky_batch_tmp/")} 2>/dev/null || true',

            # --- Launch batch job in background, wait for some progress,
            #     then cancel ---
            (
                f'( {run_launcher}\n) &\n'
                f'BGPID=$!\n'
                # Wait for the job to reach RUNNING.
                f'JOB_ID=""\n'
                f'for i in $(seq 1 90); do\n'
                f'  JOB_ID=$(sky jobs queue 2>/dev/null | '
                f'awk \'$1 ~ /^[0-9]+$/ && /RUNNING/ {{print $1; exit}}\')\n'
                f'  if [ -n "$JOB_ID" ]; then break; fi\n'
                f'  sleep 5\n'
                f'done\n'
                f'echo "Found batch job ID=$JOB_ID"\n'
                # Wait until some batches complete.
                f'for i in $(seq 1 120); do\n'
                f'  PROGRESS=$(sky jobs queue 2>/dev/null '
                f'| grep "{pool_name}" | head -1 '
                f'| grep -oE "[0-9]+/[0-9]+" | head -1)\n'
                f'  COMPLETED=${{PROGRESS%%/*}}\n'
                f'  if [ -n "$COMPLETED" ] && [ "$COMPLETED" -ge 5 ]; then\n'
                f'    break\n'
                f'  fi\n'
                f'  sleep 5\n'
                f'done\n'
                f'echo "Progress before cancel: $PROGRESS"\n'
                f'echo "$JOB_ID" > /tmp/batch-resume-jobid-{name}.txt\n'
                f'sky jobs cancel $JOB_ID -y\n'
                f'wait $BGPID 2>/dev/null || true'),

            # --- Verify job is CANCELLED ---
            (f'sky jobs queue '
             f'| grep "{pool_name}" | grep CANCELLED'),

            # --- Resume from the cancelled job ---
            (f'RESUME_FROM=$(cat /tmp/batch-resume-jobid-{name}.txt) '
             f'{run_launcher}'),

            # --- Verify resumed job SUCCEEDED ---
            # The resumed job should be the latest; check for SUCCEEDED.
            (f'sky jobs queue | grep "{pool_name}" | grep SUCCEEDED'),

            # --- Verify output has all 40 items ---
            f'{cp} {url}/output.jsonl /tmp/batch-resume-output-{name}.jsonl',
            f'test $(wc -l < /tmp/batch-resume-output-{name}.jsonl) -eq 40',
            (f"python3 << 'PYEOF'\n"
             "import json\n"
             f"path = '/tmp/batch-resume-output-{name}.jsonl'\n"
             "results = [json.loads(l) for l in open(path)]\n"
             "assert len(results) == 40, f'Expected 40, got {len(results)}'\n"
             "texts = set()\n"
             "for r in results:\n"
             "    assert 'text' in r and 'output' in r, f'Bad record: {r}'\n"
             "    assert r['output'] == r['text'] * 2, f'Wrong output: {r}'\n"
             "    texts.add(r['text'])\n"
             "expected = set(f'item_{i}' for i in range(40))\n"
             "assert texts == expected, (\n"
             "    f'Missing: {expected - texts}, Extra: {texts - expected}')\n"
             "print(f'All {len(results)} results valid after resume')\n"
             "PYEOF"),
        ],
        # Teardown
        (f'sky jobs pool down {pool_name} -y;'
         f' sky serve down {pool_name} -y 2>/dev/null || true;'
         f' {delete_bkt};'
         f' rm -f /tmp/batch-resume-input-{name}.jsonl'
         f' /tmp/batch-resume-output-{name}.jsonl'
         f' /tmp/batch-resume-jobid-{name}.txt'
         f' {script}'),
        timeout=40 * 60,
        env={
            'SKY_BATCH_BUCKET': bucket,
            'SKY_BATCH_STORE': store,
            'BATCH_POOL_NAME': pool_name,
        },
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Test batch resume from failed batches ----------
@pytest.mark.batch
@pytest.mark.no_remote_server  # see note 1 above
def test_batch_resume_failed(generic_cloud: str):
    """Run a batch job whose mapper deliberately fails, then resume.

    Verifies that:
    1. The job reaches FAILED status after batch retries exhaust.
    2. Resume from FAILED job re-dispatches failed + un-run batches.
    3. Final output contains all items with correct values.
    """
    name = smoke_tests_utils.get_cluster_name()
    bucket = f'sky-batch-rsmf-{name}'
    pool_name = f'batch-rsmf-pool-{name}'
    url, create_bkt, delete_bkt, cp, rm, _, rm_r = _storage_cmds(
        generic_cloud, bucket)
    store = 'gs' if generic_cloud == 'gcp' else 's3'

    # Script files (inspect.getsource needs file-defined functions).
    fail_script = f'/tmp/batch-rsmf-fail-launcher-{name}.py'
    resume_script = f'/tmp/batch-rsmf-resume-launcher-{name}.py'
    run_fail = f'python {fail_script}'
    run_resume = f'python {resume_script}'

    test = smoke_tests_utils.Test(
        'batch_resume_failed',
        [
            # --- Write launcher scripts to files ---
            (f"cat > {fail_script} << 'PYEOF'\n"
             "import os, sky\n"
             f"bucket = '{bucket}'\n"
             f"store = '{store}'\n"
             f"pool_name = '{pool_name}'\n"
             "input_path = f'{store}://{bucket}/test.jsonl'\n"
             "output_path = f'{store}://{bucket}/output.jsonl'\n"
             "\n"
             "@sky.batch.remote_function\n"
             "def failing_mapper():\n"
             "    for batch in sky.batch.load():\n"
             "        results = []\n"
             "        for item in batch:\n"
             "            text = item.get('text', '')\n"
             "            if text.startswith('FAIL_'):\n"
             "                raise RuntimeError('Deliberate failure')\n"
             "            results.append({'text': text, 'output': text * 2})\n"
             "        sky.batch.save_results(results)\n"
             "\n"
             "ds = sky.batch.Dataset(sky.batch.JsonReader(input_path))\n"
             "ds.map(\n"
             "    failing_mapper,\n"
             "    pool_name=pool_name,\n"
             "    batch_size=2,\n"
             "    output=sky.batch.JsonWriter(output_path),\n"
             ")\n"
             "PYEOF"),
            (f"cat > {resume_script} << 'PYEOF'\n"
             "import os, sky\n"
             f"bucket = '{bucket}'\n"
             f"store = '{store}'\n"
             f"pool_name = '{pool_name}'\n"
             "input_path = f'{store}://{bucket}/test.jsonl'\n"
             "output_path = f'{store}://{bucket}/output.jsonl'\n"
             "resume_from = int(os.environ['RESUME_FROM'])\n"
             "\n"
             "@sky.batch.remote_function\n"
             "def working_mapper():\n"
             "    for batch in sky.batch.load():\n"
             "        results = [{'text': item.get('text', ''),\n"
             "                     'output': item.get('text', '') * 2}\n"
             "                    for item in batch]\n"
             "        sky.batch.save_results(results)\n"
             "\n"
             "ds = sky.batch.Dataset(sky.batch.JsonReader(input_path))\n"
             "ds.map(\n"
             "    working_mapper,\n"
             "    pool_name=pool_name,\n"
             "    batch_size=2,\n"
             "    output=sky.batch.JsonWriter(output_path),\n"
             "    resume_from=resume_from,\n"
             ")\n"
             "PYEOF"),
            # --- Pre-cleanup: also remove stale pools from aborted runs ---
            (f'for p in $(sky jobs pool ls 2>/dev/null '
             f'| awk \'/batch-rsmf-pool-/ {{print $1}}\' | sort -u); do '
             f'sky jobs pool down "$p" -y 2>/dev/null || true; done || true'),
            f'sky jobs pool down {pool_name} -y 2>/dev/null || true',
            f'sky serve down {pool_name} -y 2>/dev/null || true',
            # --- Create pool ---
            (f's=$(sky jobs pool apply -p {pool_name} --infra {generic_cloud}'
             f' examples/batch/simple/pool.yaml -y); '
             f'echo "$s"; '
             f'echo "$s" | grep "Successfully created pool"'),
            # --- Data setup: 20 items, FAIL_ at indices 4-5 (batch 2) ---
            create_bkt,
            (f'{{ for i in $(seq 0 3); do '
             f'echo "{{\\"text\\": \\"item_$i\\"}}"; done; '
             f'echo "{{\\"text\\": \\"FAIL_0\\"}}"; '
             f'echo "{{\\"text\\": \\"FAIL_1\\"}}"; '
             f'for i in $(seq 4 17); do '
             f'echo "{{\\"text\\": \\"item_$i\\"}}"; done; '
             f'}} > /tmp/batch-rsmf-input-{name}.jsonl'),
            f'{cp} /tmp/batch-rsmf-input-{name}.jsonl {url}/test.jsonl',
            f'{rm} {url}/output.jsonl 2>/dev/null || true',
            f'{rm_r(f"{url}/.sky_batch_tmp/")} 2>/dev/null || true',

            # --- Launch failing job in background, wait for it to finish ---
            (f'( {run_fail}\n'
             f') > /tmp/batch-rsmf-fail-log-{name}.txt 2>&1 &\n'
             f'BGPID=$!\n'
             f'JOB_ID=""\n'
             f'for i in $(seq 1 90); do\n'
             f'  JOB_ID=$(sky jobs queue 2>/dev/null | '
             f'grep "{pool_name}" | '
             f'awk \'$1 ~ /^[0-9]+$/ {{print $1; exit}}\')\n'
             f'  if [ -n "$JOB_ID" ]; then break; fi\n'
             f'  sleep 5\n'
             f'done\n'
             f'echo "Failing job: $JOB_ID"\n'
             f'echo "$JOB_ID" > /tmp/batch-rsmf-jobid-{name}.txt\n'
             f'wait $BGPID 2>/dev/null || true'),

            # --- Verify FAILED ---
            (f'JOB_ID=$(cat /tmp/batch-rsmf-jobid-{name}.txt)\n'
             f'for i in $(seq 1 60); do\n'
             f'  if sky jobs queue 2>/dev/null | '
             f'awk -v id="$JOB_ID" \'$1 == id\' | '
             f'grep -q FAILED; then break; fi\n'
             f'  sleep 5\n'
             f'done\n'
             f'sky jobs queue | '
             f'awk -v id="$(cat /tmp/batch-rsmf-jobid-{name}.txt)" '
             f"'$1 == id' | grep FAILED"),

            # --- Resume from FAILED job with working mapper ---
            (f'RESUME_FROM=$(cat /tmp/batch-rsmf-jobid-{name}.txt) '
             f'{run_resume}'),

            # --- Verify resumed job SUCCEEDED ---
            f'sky jobs queue | grep "{pool_name}" | grep SUCCEEDED',

            # --- Verify output has all 20 items ---
            f'{cp} {url}/output.jsonl /tmp/batch-rsmf-output-{name}.jsonl',
            f'test $(wc -l < /tmp/batch-rsmf-output-{name}.jsonl) -eq 20',
            (f"python3 << 'PYEOF'\n"
             "import json\n"
             f"path = '/tmp/batch-rsmf-output-{name}.jsonl'\n"
             "results = [json.loads(l) for l in open(path)]\n"
             "assert len(results) == 20, f'Expected 20, got {len(results)}'\n"
             "texts = set()\n"
             "for r in results:\n"
             "    assert 'text' in r and 'output' in r, f'Bad record: {r}'\n"
             "    assert r['output'] == r['text'] * 2, f'Wrong output: {r}'\n"
             "    texts.add(r['text'])\n"
             "expected = (set(f'item_{i}' for i in range(18))\n"
             "            | {'FAIL_0', 'FAIL_1'})\n"
             "assert texts == expected, (\n"
             "    f'Missing: {expected - texts}, Extra: {texts - expected}')\n"
             "print(f'All {len(results)} results valid after resume "
             "from FAILED')\n"
             "PYEOF"),
        ],
        # Teardown
        (f'sky jobs pool down {pool_name} -y;'
         f' sky serve down {pool_name} -y 2>/dev/null || true;'
         f' {delete_bkt};'
         f' rm -f /tmp/batch-rsmf-input-{name}.jsonl'
         f' /tmp/batch-rsmf-output-{name}.jsonl'
         f' /tmp/batch-rsmf-jobid-{name}.txt'
         f' /tmp/batch-rsmf-fail-log-{name}.txt'
         f' {fail_script} {resume_script}'),
        timeout=40 * 60,
        env={
            'SKY_BATCH_BUCKET': bucket,
            'SKY_BATCH_STORE': store,
            'BATCH_POOL_NAME': pool_name,
        },
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Test resume from RUNNING rejected, cancel, then resume ----------
@pytest.mark.batch
@pytest.mark.no_remote_server  # see note 1 above
def test_batch_resume_running_guard(generic_cloud: str):
    """Try resume from RUNNING job, verify rejection, cancel, then resume.

    Verifies that:
    1. Resume from a RUNNING job is rejected (managed job fails).
    2. After cancelling the original, resume succeeds.
    3. Final output contains all items.
    """
    name = smoke_tests_utils.get_cluster_name()
    bucket = f'sky-batch-rsgd-{name}'
    pool_name = f'batch-rsgd-pool-{name}'
    url, create_bkt, delete_bkt, cp, rm, _, rm_r = _storage_cmds(
        generic_cloud, bucket)
    store = 'gs' if generic_cloud == 'gcp' else 's3'

    script = f'/tmp/batch-rsgd-launcher-{name}.py'
    run_launcher = f'python {script}'

    test = smoke_tests_utils.Test(
        'batch_resume_running_guard',
        [
            # --- Write launcher script to file ---
            (f"cat > {script} << 'PYEOF'\n"
             "import os, sky\n"
             f"bucket = '{bucket}'\n"
             f"store = '{store}'\n"
             f"pool_name = '{pool_name}'\n"
             "input_path = f'{store}://{bucket}/test.jsonl'\n"
             "output_path = f'{store}://{bucket}/output.jsonl'\n"
             "\n"
             "@sky.batch.remote_function\n"
             "def double_text():\n"
             "    for batch in sky.batch.load():\n"
             "        results = [{'text': item.get('text', ''),\n"
             "                     'output': item.get('text', '') * 2}\n"
             "                    for item in batch]\n"
             "        sky.batch.save_results(results)\n"
             "\n"
             "resume_from = os.environ.get('RESUME_FROM')\n"
             "resume_from = int(resume_from) if resume_from else None\n"
             "ds = sky.batch.Dataset(sky.batch.JsonReader(input_path))\n"
             "ds.map(\n"
             "    double_text,\n"
             "    pool_name=pool_name,\n"
             "    batch_size=2,\n"
             "    output=sky.batch.JsonWriter(output_path),\n"
             "    resume_from=resume_from,\n"
             ")\n"
             "PYEOF"),
            # --- Pre-cleanup: also remove stale pools from aborted runs ---
            (f'for p in $(sky jobs pool ls 2>/dev/null '
             f'| awk \'/batch-rsgd-pool-/ {{print $1}}\' | sort -u); do '
             f'sky jobs pool down "$p" -y 2>/dev/null || true; done || true'),
            f'sky jobs pool down {pool_name} -y 2>/dev/null || true',
            f'sky serve down {pool_name} -y 2>/dev/null || true',
            # --- Create pool ---
            (f's=$(sky jobs pool apply -p {pool_name} --infra {generic_cloud}'
             f' examples/batch/simple/pool.yaml -y); '
             f'echo "$s"; '
             f'echo "$s" | grep "Successfully created pool"'),
            # --- Data setup: 20 items (10 batches of 2) ---
            create_bkt,
            (f'for i in $(seq 0 19); do '
             f'echo "{{\\"text\\": \\"item_$i\\"}}"; '
             f'done > /tmp/batch-rsgd-input-{name}.jsonl'),
            f'{cp} /tmp/batch-rsgd-input-{name}.jsonl {url}/test.jsonl',
            f'{rm} {url}/output.jsonl 2>/dev/null || true',
            f'{rm_r(f"{url}/.sky_batch_tmp/")} 2>/dev/null || true',

            # --- Launch batch job in background, wait for RUNNING ---
            (f'( {run_launcher}\n'
             f') > /tmp/batch-rsgd-bg-{name}.log 2>&1 &\n'
             f'BGPID=$!\n'
             f'echo $BGPID > /tmp/batch-rsgd-bgpid-{name}.txt\n'
             f'JOB_ID=""\n'
             f'for i in $(seq 1 90); do\n'
             f'  JOB_ID=$(sky jobs queue 2>/dev/null | '
             f'grep "{pool_name}" | '
             f'awk \'$1 ~ /^[0-9]+$/ && /RUNNING/ '
             f'{{print $1; exit}}\')\n'
             f'  if [ -n "$JOB_ID" ]; then break; fi\n'
             f'  sleep 5\n'
             f'done\n'
             f'if [ -z "$JOB_ID" ]; then\n'
             f'  echo "ERROR: no RUNNING job found"\n'
             f'  exit 1\n'
             f'fi\n'
             f'echo "$JOB_ID" > /tmp/batch-rsgd-jobid-{name}.txt\n'
             f'echo "Found running job: $JOB_ID"'),

            # --- Try resume from RUNNING job — should fail ---
            (f'( RESUME_FROM=$(cat /tmp/batch-rsgd-jobid-{name}.txt) '
             f'{run_launcher}\n'
             f') > /tmp/batch-rsgd-resume-log-{name}.txt 2>&1; '
             f'RESUME_EXIT=$?\n'
             f'if [ $RESUME_EXIT -eq 0 ]; then\n'
             f'  echo "ERROR: resume from RUNNING should have failed"\n'
             f'  exit 1\n'
             f'fi\n'
             f'echo "Resume from RUNNING correctly rejected '
             f'(exit=$RESUME_EXIT)"'),

            # --- Cancel the original job ---
            (f'JOB_ID=$(cat /tmp/batch-rsgd-jobid-{name}.txt)\n'
             f'sky jobs cancel $JOB_ID -y\n'
             f'for i in $(seq 1 60); do\n'
             f'  if sky jobs queue 2>/dev/null | '
             f'awk -v id="$JOB_ID" \'$1 == id\' | '
             f'grep -q CANCELLED; then break; fi\n'
             f'  sleep 5\n'
             f'done\n'
             f'kill $(cat /tmp/batch-rsgd-bgpid-{name}.txt) '
             f'2>/dev/null || true'),

            # --- Verify original is CANCELLED ---
            (f'sky jobs queue | '
             f'awk -v id="$(cat /tmp/batch-rsgd-jobid-{name}.txt)" '
             f"'$1 == id' | grep CANCELLED"),

            # --- Resume from CANCELLED job ---
            (f'RESUME_FROM=$(cat /tmp/batch-rsgd-jobid-{name}.txt) '
             f'{run_launcher}'),

            # --- Verify resumed job SUCCEEDED ---
            f'sky jobs queue | grep "{pool_name}" | grep SUCCEEDED',

            # --- Verify output has all 20 items ---
            f'{cp} {url}/output.jsonl /tmp/batch-rsgd-output-{name}.jsonl',
            f'test $(wc -l < /tmp/batch-rsgd-output-{name}.jsonl) -eq 20',
            (f"python3 << 'PYEOF'\n"
             "import json\n"
             f"path = '/tmp/batch-rsgd-output-{name}.jsonl'\n"
             "results = [json.loads(l) for l in open(path)]\n"
             "assert len(results) == 20, f'Expected 20, got "
             "{len(results)}'\n"
             "texts = set()\n"
             "for r in results:\n"
             "    assert 'text' in r and 'output' in r, f'Bad record: {r}'\n"
             "    assert r['output'] == r['text'] * 2, f'Wrong output: {r}'\n"
             "    texts.add(r['text'])\n"
             "expected = set(f'item_{i}' for i in range(20))\n"
             "assert texts == expected, (\n"
             "    f'Missing: {expected - texts}, Extra: {texts - expected}')\n"
             "print(f'All {len(results)} results valid after resume')\n"
             "PYEOF"),
        ],
        # Teardown
        (f'sky jobs pool down {pool_name} -y;'
         f' sky serve down {pool_name} -y 2>/dev/null || true;'
         f' {delete_bkt};'
         f' rm -f /tmp/batch-rsgd-input-{name}.jsonl'
         f' /tmp/batch-rsgd-output-{name}.jsonl'
         f' /tmp/batch-rsgd-jobid-{name}.txt'
         f' /tmp/batch-rsgd-bgpid-{name}.txt'
         f' /tmp/batch-rsgd-bg-{name}.log'
         f' /tmp/batch-rsgd-resume-log-{name}.txt'
         f' {script}'),
        timeout=40 * 60,
        env={
            'SKY_BATCH_BUCKET': bucket,
            'SKY_BATCH_STORE': store,
            'BATCH_POOL_NAME': pool_name,
        },
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Test batch HA: kill controller, verify resume from DB ----------
@pytest.mark.kubernetes
@pytest.mark.batch
@pytest.mark.no_remote_server  # see note 1 above
def test_batch_ha_kill_running(generic_cloud: str):
    """Kill the jobs controller while a batch job is RUNNING.

    After the controller pod restarts, the batch coordinator should
    resume from DB (``_resume_from_db``) and complete all batches.
    Uses the simple double-text example (CPU-only) to keep costs low.
    """
    if smoke_tests_utils.is_non_docker_remote_api_server():
        pytest.skip(
            'Skipping HA test in non-docker remote api server environment as '
            'controller might be managed by different user/test agents')
    if smoke_tests_utils.server_side_is_consolidation_mode():
        pytest.skip('Skipping HA kill test in consolidation mode: no separate '
                    'controller pod to kill')

    name = smoke_tests_utils.get_cluster_name()
    bucket = f'sky-batch-ha-{name}'
    pool_name = 'test-batch-pool'
    url, create_bkt, delete_bkt, cp, rm, _, rm_r = _storage_cmds(
        generic_cloud, bucket)
    store = 'gs' if generic_cloud == 'gcp' else 's3'

    # HA config: run the jobs controller on k8s with high_availability.
    skypilot_config_path = 'tests/test_yamls/managed_jobs_ha_config.yaml'
    pytest_config_file_override = (
        smoke_tests_utils.pytest_config_file_override())
    if pytest_config_file_override is not None:
        with open(pytest_config_file_override, 'r') as f:
            base_config = f.read()
        with open(skypilot_config_path, 'r') as f:
            ha_config = f.read()
        with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w',
                                         delete=False) as f:
            f.write(base_config)
            f.write(ha_config)
            f.flush()
            skypilot_config_path = f.name

    test = smoke_tests_utils.Test(
        'batch_ha_kill_running',
        [
            # --- Cloud-cmd cluster for kubectl access ---
            smoke_tests_utils.launch_cluster_for_cloud_cmd(generic_cloud, name),
            # --- Pre-cleanup ---
            f'sky jobs pool down {pool_name} -y 2>/dev/null || true',
            f'sky serve down {pool_name} -y 2>/dev/null || true',
            # --- Create pool with generic_cloud ---
            (f's=$(sky jobs pool apply -p {pool_name} --infra {generic_cloud}'
             f' examples/batch/simple/pool.yaml -y); '
             f'echo "$s"; '
             f'echo "$s" | grep "Successfully created pool"'),
            # --- Data setup: 60 items / batch_size 2 = 30 batches ---
            create_bkt,
            (f'for i in $(seq 1 60); do '
             f'echo "{{\\"text\\": \\"word_$i\\"}}"; '
             f'done > /tmp/batch-ha-input-{name}.jsonl'),
            f'{cp} /tmp/batch-ha-input-{name}.jsonl {url}/test.jsonl',
            f'{rm} {url}/output.jsonl 2>/dev/null || true',
            f'{rm_r(f"{url}/.sky_batch_tmp/")} 2>/dev/null || true',

            # --- Launch batch job in background, wait for RUNNING ---
            (f'nohup python examples/batch/simple/double_text.py '
             f'> /tmp/batch-ha-bg-{name}.log 2>&1 &\n'
             f'echo "Backgrounded double_text.py (PID=$!)"\n'
             f'for i in $(seq 1 180); do\n'
             f'  if sky jobs queue 2>/dev/null '
             f'| grep "{pool_name}" | grep -q "RUNNING"; then\n'
             f'    echo "Batch job is RUNNING"\n'
             f'    exit 0\n'
             f'  fi\n'
             f'  sleep 5\n'
             f'done\n'
             f'echo "Timeout waiting for batch job to reach RUNNING"\n'
             f'exit 1'),

            # --- Poll until some batches complete, then record progress ---
            # Workers need time to start; poll instead of a fixed sleep.
            (
                f'echo "Waiting for batches to start completing..."\n'
                f'for i in $(seq 1 120); do\n'
                f'  QUEUE=$(sky jobs queue 2>/dev/null)\n'
                f'  POOL_LINE=$(echo "$QUEUE" '
                f'| grep "{pool_name}" | head -1)\n'
                f'  PROGRESS=$(echo "$POOL_LINE" '
                f'| grep -oE "[0-9]+/[0-9]+" | head -1)\n'
                f'  COMPLETED=${{PROGRESS%%/*}}\n'
                f'  if [ -n "$COMPLETED" ] && [ "$COMPLETED" -gt 0 ]; then\n'
                f'    break\n'
                f'  fi\n'
                f'  sleep 5\n'
                f'done\n'
                # Once progress is non-zero, let a few more batches complete
                # so we have a meaningful checkpoint to verify after resume.
                f'echo "First batch completed, sleeping 60s for more progress"\n'
                f'sleep 60\n'
                f'QUEUE=$(sky jobs queue 2>/dev/null)\n'
                f'POOL_LINE=$(echo "$QUEUE" | grep "{pool_name}" | head -1)\n'
                f'PROGRESS=$(echo "$POOL_LINE" '
                f'| grep -oE "[0-9]+/[0-9]+" | head -1)\n'
                f'COMPLETED=${{PROGRESS%%/*}}\n'
                # Record the managed job ID for log verification later.
                f'JOB_ID=$(echo "$POOL_LINE" '
                f'| awk \'$1 ~ /^[0-9]+$/ {{print $1}}\')\n'
                f'echo "Progress before kill: $PROGRESS '
                f'(completed=$COMPLETED, job_id=$JOB_ID)"\n'
                f'if [ -z "$COMPLETED" ] || [ "$COMPLETED" -eq 0 ]; then\n'
                f'  echo "ERROR: No batches completed"\n'
                f'  exit 1\n'
                f'fi\n'
                f'echo "$COMPLETED" '
                f'> /tmp/batch-ha-progress-{name}.txt\n'
                f'echo "$JOB_ID" '
                f'> /tmp/batch-ha-jobid-{name}.txt'),

            # --- Kill controller pod ---
            smoke_tests_utils.kill_and_wait_controller(name, 'jobs'),

            # --- Verify resume preserves progress, then wait for SUCCEED ---
            # Bound progress-based, not wall-clock based.  The HA
            # guarantee is "resumed progress preserved AND forward
            # progress continues" — bail fast if progress stalls for
            # STALL_LIMIT iterations (~10 min), but keep polling as long
            # as we keep seeing new batches complete.
            (
                f'COMPLETED_BEFORE=$(cat /tmp/batch-ha-progress-{name}.txt)\n'
                f'echo "Completed before kill: $COMPLETED_BEFORE"\n'
                f'VERIFIED=0\n'
                f'LAST_PROGRESS=$COMPLETED_BEFORE\n'
                f'STALL=0\n'
                f'STALL_LIMIT=120\n'  # 120 * 5s = 10 min without progress
                f'MAX_ITERS=600\n'  # hard cap: 50 min
                f'for i in $(seq 1 $MAX_ITERS); do\n'
                f'  LINE=$(sky jobs queue 2>/dev/null '
                f'| grep "{pool_name}" | head -1)\n'
                f'  if echo "$LINE" | grep -qE "FAILED"; then\n'
                f'    echo "ERROR: Batch job FAILED after recovery"\n'
                f'    echo "Job line: $LINE"\n'
                f'    exit 1\n'
                f'  fi\n'
                f'  if echo "$LINE" | grep -qE "RUNNING|WINDING_DOWN|SUCCEEDED"; then\n'
                f'    PROGRESS_AFTER=$(echo "$LINE" '
                f'| grep -oE "[0-9]+/[0-9]+" | head -1)\n'
                f'    COMPLETED_AFTER=${{PROGRESS_AFTER%%/*}}\n'
                f'    if [ "$VERIFIED" -eq 0 ]; then\n'
                f'      echo "Progress after recovery: $PROGRESS_AFTER"\n'
                # When the job is in WINDING_DOWN, the progress column
                # shows "Winding down" instead of "N/M", so
                # COMPLETED_AFTER will be empty.  In that case all
                # batches have been dispatched, so progress is
                # preserved by definition.
                f'      if [ -z "$COMPLETED_AFTER" ]; then\n'
                f'        echo "Job is winding down, all batches dispatched"\n'
                f'        VERIFIED=1\n'
                # After resume the completed count must be >= what we
                # recorded before the kill.  It can be slightly larger
                # because batches may have finished between our last
                # progress read and the actual kill.
                f'      elif [ "$COMPLETED_AFTER" -ge "$COMPLETED_BEFORE" ]; then\n'
                f'        echo "Resume verified: progress preserved '
                f'($COMPLETED_BEFORE -> $COMPLETED_AFTER)"\n'
                f'        VERIFIED=1\n'
                f'      else\n'
                f'        echo "ERROR: Progress went backwards after '
                f'recovery ($COMPLETED_BEFORE -> $COMPLETED_AFTER)"\n'
                f'        exit 1\n'
                f'      fi\n'
                f'    fi\n'
                # Detect stalled progress once VERIFIED (skip during
                # WINDING_DOWN where COMPLETED_AFTER is empty).
                f'    if [ "$VERIFIED" -eq 1 ] && [ -n "$COMPLETED_AFTER" ]; then\n'
                f'      if [ "$COMPLETED_AFTER" -gt "$LAST_PROGRESS" ]; then\n'
                f'        LAST_PROGRESS=$COMPLETED_AFTER\n'
                f'        STALL=0\n'
                f'      else\n'
                f'        STALL=$((STALL + 1))\n'
                f'        if [ "$STALL" -ge "$STALL_LIMIT" ]; then\n'
                f'          echo "ERROR: Job progress stalled at '
                f'$LAST_PROGRESS/30 for $((STALL * 5))s after recovery"\n'
                f'          exit 1\n'
                f'        fi\n'
                f'      fi\n'
                f'    fi\n'
                f'    if echo "$LINE" | grep -q "SUCCEEDED"; then\n'
                f'      echo "Batch job SUCCEEDED after controller recovery"\n'
                f'      exit 0\n'
                f'    fi\n'
                f'  fi\n'
                f'  sleep 5\n'
                f'done\n'
                f'echo "Timeout waiting for SUCCEEDED after recovery"\n'
                f'exit 1'),

            # --- Verify controller logs show BATCH_RESUME with correct count ---
            (
                f'JOB_ID=$(cat /tmp/batch-ha-jobid-{name}.txt)\n'
                f'COMPLETED_BEFORE=$(cat /tmp/batch-ha-progress-{name}.txt)\n'
                f's=$(sky jobs logs --controller $JOB_ID --no-follow)\n'
                f'echo "$s"\n'
                # The BATCH_RESUME line is emitted by _resume_from_db:
                # "BATCH_RESUME total=100 completed=N pending=M"
                f'echo "$s" | grep "BATCH_RESUME" || '
                f'{{ echo "ERROR: BATCH_RESUME not found in controller logs"; '
                f'exit 1; }}\n'
                f'RESUME_COMPLETED=$(echo "$s" | grep "BATCH_RESUME" '
                f'| grep -oE "completed=[0-9]+" | cut -d= -f2)\n'
                f'echo "Controller log: resumed with '
                f'completed=$RESUME_COMPLETED (expected >= $COMPLETED_BEFORE)"\n'
                f'if [ -z "$RESUME_COMPLETED" ] || '
                f'[ "$RESUME_COMPLETED" -lt "$COMPLETED_BEFORE" ]; then\n'
                f'  echo "ERROR: Resume completed count '
                f'($RESUME_COMPLETED) < progress before kill '
                f'($COMPLETED_BEFORE)"\n'
                f'  exit 1\n'
                f'fi'),

            # --- Verify all 60 items processed correctly ---
            f'{cp} {url}/output.jsonl /tmp/batch-ha-output-{name}.jsonl',
            f'test $(wc -l < /tmp/batch-ha-output-{name}.jsonl) -eq 60',
            (f"python3 << 'PYEOF'\n"
             "import json\n"
             f"path = '/tmp/batch-ha-output-{name}.jsonl'\n"
             "results = [json.loads(l) for l in open(path)]\n"
             "assert len(results) == 60, f'Expected 60, got {len(results)}'\n"
             "texts = set()\n"
             "for r in results:\n"
             "    assert 'text' in r and 'output' in r, f'Bad record: {r}'\n"
             "    assert r['output'] == r['text'] * 2, f'Wrong output: {r}'\n"
             "    texts.add(r['text'])\n"
             "expected = set(f'word_{i}' for i in range(1, 61))\n"
             "assert texts == expected, (\n"
             "    f'Missing: {expected - texts}, Extra: {texts - expected}')\n"
             "print(f'All {len(results)} results valid after HA recovery')\n"
             "PYEOF"),
        ],
        # Teardown: remove pool, bucket, temp files, cloud-cmd cluster.
        (f'sky jobs pool down {pool_name} -y;'
         f' sky serve down {pool_name} -y 2>/dev/null || true;'
         f' {delete_bkt};'
         f' rm -f /tmp/batch-ha-input-{name}.jsonl'
         f' /tmp/batch-ha-output-{name}.jsonl'
         f' /tmp/batch-ha-bg-{name}.log'
         f' /tmp/batch-ha-progress-{name}.txt'
         f' /tmp/batch-ha-jobid-{name}.txt;'
         f' {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}'),
        timeout=60 * 60,
        env={
            'SKY_BATCH_BUCKET': bucket,
            'SKY_BATCH_STORE': store,
            skypilot_config.ENV_VAR_SKYPILOT_CONFIG: skypilot_config_path,
        },
    )
    smoke_tests_utils.run_one_test(test)
