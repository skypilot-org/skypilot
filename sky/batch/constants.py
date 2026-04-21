"""Constants for Sky Batch."""

# Worker service (localhost HTTP on each worker node)
WORKER_SERVICE_PORT = 8290
WORKER_SERVICE_STARTUP_TIMEOUT = 60  # seconds to wait for service health

# Timeouts (in seconds)
WORKER_DISCOVERY_TIMEOUT = 300
# On resume, batches are already checkpointed so we can afford to wait longer
# for pool workers to reappear while the controller pod and the serve-side
# pool status plumbing stabilize after a restart.  Don't make this too large
# though: if the controller pod is stuck in a restart loop, we want the run
# to fail fast enough for the next attempt to take over.
WORKER_DISCOVERY_RESUME_TIMEOUT = 600
BATCH_COMPLETION_TIMEOUT = 3600  # 1 hour max per batch

# Grace period after cancelling stale worker jobs before launching fresh
# workers.  ``sdk.cancel`` only sends SIGTERM; the Python HTTP service
# holding port 8290 needs a moment to actually exit and release the port.
STALE_WORKER_GRACE_PERIOD = 15

# Polling interval for sdk.job_status() when waiting for batch completion
BATCH_POLL_INTERVAL = 5

# Retry settings
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # Exponential backoff base

# Naming pattern for result batch files
# e.g., batch_00000000-00000031.jsonl for indices 0-31
BATCH_NAME_PATTERN = 'batch_{start:08d}-{end:08d}.jsonl'

# Naming pattern for intermediate input batch files
# e.g., input_batch_00000000-00000031.jsonl for indices 0-31
INPUT_BATCH_NAME_PATTERN = 'input_batch_{start:08d}-{end:08d}.jsonl'

# Temporary directory name for intermediate results
TEMP_DIR_NAME = '.sky_batch_tmp'
