"""Currently doesn't work due to engine not running >1 tasks."""
import sky

with sky.Dag() as dag:
    tasks = [
        sky.Task(run='echo {}'.format(i)).set_resources(
            sky.Resources(sky.clouds.AWS(), 'p3.2xlarge')) for i in range(5)
    ]
dag = sky.Optimizer.optimize(dag)
sky.execute(dag, dryrun=True)
