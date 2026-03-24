"""Constants for Sky Batch."""

# Worker service (localhost HTTP on each worker node)
WORKER_SERVICE_PORT = 8290
WORKER_SERVICE_STARTUP_TIMEOUT = 60  # seconds to wait for service health

# Timeouts (in seconds)
WORKER_DISCOVERY_TIMEOUT = 300
BATCH_COMPLETION_TIMEOUT = 3600  # 1 hour max per batch

# Polling interval for sdk.job_status() when waiting for batch completion
BATCH_POLL_INTERVAL = 5

# Retry settings
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # Exponential backoff base

# Chunk naming pattern for result files
# e.g., chunk_00000000-00000031.jsonl for indices 0-31
CHUNK_NAME_PATTERN = 'chunk_{start:08d}-{end:08d}.jsonl'

# Input chunk naming pattern for intermediate input files
# e.g., input_chunk_00000000-00000031.jsonl for indices 0-31
INPUT_CHUNK_NAME_PATTERN = 'input_chunk_{start:08d}-{end:08d}.jsonl'

# Temporary directory name for intermediate results
TEMP_DIR_NAME = '.sky_batch_tmp'
