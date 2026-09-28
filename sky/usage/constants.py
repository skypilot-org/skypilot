"""Constants for usage collection."""
import os

LOG_URL = os.environ.get('SKYPILOT_USAGE_LOG_URL',
                         'https://usage-v2.skypilot.co')

# Scarf (https://scarf.sh) analytics gateway, receiving a fire-and-forget
# ping with the entrypoint name and SkyPilot version. See
# usage_lib._maybe_start_scarf_ping.
SCARF_GATEWAY_URL = os.environ.get('SKYPILOT_SCARF_GATEWAY_URL',
                                   'https://ossapi.skypilot.ai/sky-launch')

USAGE_MESSAGE_SCHEMA_VERSION = 1
PRIVACY_POLICY_PATH = '~/.sky/privacy_policy'

# How long a recorded GPU capacity row stays valid. Installed capacity
# changes on the timescale of node pool edits, so a fleet-size metric does not
# need per-heartbeat freshness; at the 10 minute heartbeat interval this is
# six heartbeats per query. Rows live in the kv_cache DB table and expire on
# their own. This cache backs telemetry only — scheduling and `sky status`
# continue to read node info directly, and must never be served from here.
NODE_INFO_CACHE_TTL_SECONDS = 3600  # 1 hour

# kv_cache key prefix for the per-infra capacity rows. Ids under it look like
# 'kubernetes/<context>' and 'slurm/<cluster>'.
NODE_INFO_CACHE_KEY_PREFIX = 'usage/gpu_capacity/'

USAGE_POLICY_MESSAGE = (
    'SkyPilot collects usage data to improve its services. '
    '`setup` and `run` commands are not collected to '
    'ensure privacy.\n'
    'Usage logging can be disabled by setting the '
    'environment variable SKYPILOT_DISABLE_USAGE_COLLECTION=1.')

USAGE_MESSAGE_REDACT_KEYS = ['setup', 'run', 'envs', 'secrets']
USAGE_MESSAGE_REDACT_TYPES = {str, dict}

# Env var for the usage run id. This is used by the API server to associate
# the usage run id of a request from client to the actual functions invoked.
USAGE_RUN_ID_ENV_VAR = 'SKYPILOT_USAGE_RUN_ID'

# The file stores the usage run id on a remote cluster, so that the heartbeat
# on that remote cluster can be associated with the usage run id. This file is
# initialized when the cluster is firstly launched in:
# sky.provision.instance_setup.start_skylet_on_head_node
USAGE_RUN_ID_FILE = '~/.sky/usage_run_id'
