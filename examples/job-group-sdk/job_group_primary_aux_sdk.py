"""
Launch a Job Group with primary/auxiliary tasks using the Python SDK.

This script demonstrates the primary/auxiliary task pattern: a primary
trainer task drives the job lifecycle, while an auxiliary data-server
task is automatically terminated after the trainer completes.

Usage:
    python examples/job-group-sdk/job_group_primary_aux_sdk.py
"""
import sky

# -- Tasks -----------------------------------------------------------

trainer = sky.Task(
    name='trainer',
    run="""
echo "Trainer starting"
echo "Training for 30 seconds..."
sleep 30
echo "Training complete"
""",
)
trainer.set_resources(sky.Resources(cpus=2, infra='kubernetes'))

data_server = sky.Task(
    name='data-server',
    run="""
echo "Data server starting on port 8000"
python3 -m http.server 8000
""",
)
data_server.set_resources(sky.Resources(cpus=2, infra='kubernetes'))

# -- DAG (Job Group) -------------------------------------------------

with sky.Dag() as dag:
    dag.add(trainer)
    dag.add(data_server)
dag.name = 'train-with-services'
dag.set_execution(sky.DagExecution.PARALLEL)

# When the trainer finishes, the data-server will be terminated after
# a 10-second grace period.
dag.primary_tasks = ['trainer']
dag.termination_delay = '10s'

# -- Launch -----------------------------------------------------------

sky.stream_and_get(sky.jobs.launch(dag))
