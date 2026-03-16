"""
Launch a Job Group using the SkyPilot Python SDK.

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
    run="""
echo "Server starting on port 8080"
python3 -m http.server 8080 &
SERVER_PID=$!
sleep 60
kill $SERVER_PID 2>/dev/null || true
echo "Server done"
""",
)
server.set_resources(sky.Resources(cpus=2, infra='kubernetes'))

client = sky.Task(
    name='client',
    run="""
echo "Client starting"
sleep 10
SERVER_HOST="server-0.${SKYPILOT_JOBGROUP_NAME}"
echo "Connecting to $SERVER_HOST:8080"
for i in $(seq 1 10); do
  if curl -s --connect-timeout 5 http://$SERVER_HOST:8080/ > /dev/null 2>&1; then
    echo "SUCCESS: Connected to server"
    exit 0
  fi
  echo "Attempt $i failed, retrying..."
  sleep 3
done
echo "FAILED: Could not connect to server"
exit 1
""",
)
client.set_resources(sky.Resources(cpus=2, infra='kubernetes'))

# -- DAG (Job Group) -------------------------------------------------

with sky.Dag() as dag:
    dag.add(server)
    dag.add(client)
dag.name = 'server-client'
dag.set_execution(sky.DagExecution.PARALLEL)

# -- Launch -----------------------------------------------------------

sky.stream_and_get(sky.jobs.launch(dag))
