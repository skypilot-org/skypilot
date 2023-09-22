import sky

def test_single_region():
    
    dag = sky.Dag()
    task = sky.Task(run='nvidia-smi')
    dag.add(task)
    
    task.set_resources([
        sky.Resources(cloud=sky.clouds.GCP(), region=['us-central1'], accelerators={'V100': 1}),
    ])

    dag = sky.optimize(dag, quiet=True)
    assert task.best_resources.region == 'us-central1'

def test_multiple_regions():
    
    dag = sky.Dag()
    task = sky.Task(run='nvidia-smi')
    dag.add(task)
    
    task.set_resources([
        sky.Resources(cloud=sky.clouds.GCP(), region=['europe-central2-a', 'asia-east1'], accelerators={'V100': 1}),
    ])

    dag = sky.optimize(dag, quiet=True)
    assert task.best_resources.region == 'asia-east1'

test_single_region()
test_multiple_regions()