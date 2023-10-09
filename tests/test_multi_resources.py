import sky


def test_ordered_resources():

    dag = sky.Dag()
    task = sky.Task(run='nvidia-smi')
    dag.add(task)

    task.set_resources([
        sky.Resources(accelerators={'V100': 1}),
        sky.Resources(accelerators={'T4': 1}),
        sky.Resources(accelerators={'V100': 1}),
        sky.Resources(accelerators={'K80': 1}),
        sky.Resources(accelerators={'T4': 4}),
    ])

    dag = sky.optimize(dag, quiet=True)
    # 'V100' is picked because it is the first in the list.
    assert 'V100' in repr(task.best_resources)


def test_unordered_resources():

    dag = sky.Dag()
    task = sky.Task(run='nvidia-smi')
    dag.add(task)

    task.set_resources({
        sky.Resources(accelerators={'V100': 1}),
        sky.Resources(accelerators={'T4': 1}),
        sky.Resources(accelerators={'V100': 1}),
        sky.Resources(accelerators={'K80': 1}),
        sky.Resources(accelerators={'T4': 4}),
    })

    dag = sky.optimize(dag, quiet=True)
    # 'K80' is picked because it is the cheapest.
    assert 'K80' in repr(task.best_resources)
