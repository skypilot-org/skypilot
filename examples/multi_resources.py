import sky

task = sky.Task(run='nvidia-smi')


def test_demand_spot_mix_resources_list():
    task.set_resources([
        sky.Resources(accelerators={'V100': 1}, use_spot=True),
        sky.Resources(accelerators={'T4': 1}),
        sky.Resources(accelerators={'V100': 1}),
        sky.Resources(accelerators={'K80': 1}),
        sky.Resources(accelerators={'T4': 4}),
    ],
                       is_resources_ordered=True)

    sky.launch(task,
               cluster_name=f'my-cluster',
               stream_logs=False,
               down=False,
               dryrun=True)


def test_ordered_resources_list():
    task.set_resources([
        sky.Resources(accelerators={'T4': 1}),
        sky.Resources(accelerators={'V100': 1}),
        sky.Resources(accelerators={'K80': 1}),
        sky.Resources(accelerators={'T4': 4}),
    ],
                       is_resources_ordered=True)

    sky.launch(task,
               cluster_name=f'my-cluster',
               stream_logs=False,
               down=False,
               dryrun=True)


def test_unordered_resources_list():
    task.set_resources({
        sky.Resources(accelerators={'T4': 1}),
        sky.Resources(accelerators={'V100': 1}),
        sky.Resources(accelerators={'K80': 1}),
        sky.Resources(accelerators={'T4': 4}),
    })

    sky.launch(task,
               cluster_name=f'my-cluster',
               stream_logs=False,
               down=False,
               dryrun=True)


if __name__ == "__main__":

    test_demand_spot_mix_resources_list()
    test_ordered_resources_list()
    test_unordered_resources_list()
