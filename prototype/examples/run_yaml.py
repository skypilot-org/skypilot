import sky
from sky import clouds

import time_estimators

with sky.Dag() as dag:
    task = sky.Task.from_yaml('examples/resnet.yaml')

dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
# sky.execute(dag, dryrun=True)
sky.execute(dag)
