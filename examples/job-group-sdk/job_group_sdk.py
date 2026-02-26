"""Launch a Job Group using the SkyPilot Python SDK.

This script builds the same server-client Job Group defined in
job_group.yaml, but entirely in Python. A server task starts an HTTP
server and a client task connects to it using Job Group service
discovery.

Usage:
    python examples/job-group-sdk/job_group_sdk.py
"""
import sky

# -- Tasks -----------------------------------------------------------

server = sky.Task(
    name='server',
    run=('echo "Server starting on port 8080"\n'
         'python3 -m http.server 8080 &\n'
         'SERVER_PID=$!\n'
         'sleep 120\n'
         'kill $SERVER_PID 2>/dev/null || true\n'
         'echo "Server done"'),
)
server.set_resources(sky.Resources(cpus=2, infra='kubernetes'))

client = sky.Task(
    name='client',
    run=('echo "Client starting"\n'
         'sleep 10\n'
         'SERVER_HOST="server-0.${SKYPILOT_JOBGROUP_NAME}"\n'
         'echo "Connecting to $SERVER_HOST:8080"\n'
         'for i in $(seq 1 10); do\n'
         '  if curl -s --connect-timeout 5 '
         'http://$SERVER_HOST:8080/ > /dev/null 2>&1; then\n'
         '    echo "SUCCESS: Connected to server"\n'
         '    exit 0\n'
         '  fi\n'
         '  echo "Attempt $i failed, retrying..."\n'
         '  sleep 3\n'
         'done\n'
         'echo "FAILED: Could not connect to server"\n'
         'exit 1'),
)
client.set_resources(sky.Resources(cpus=2, infra='kubernetes'))

# -- DAG (Job Group) -------------------------------------------------

with sky.Dag() as dag:
    dag.add(server)
    dag.add(client)
dag.name = 'server-client'
dag.set_execution(sky.DagExecution.PARALLEL)

# -- Launch -----------------------------------------------------------

sky.jobs.launch(dag)
