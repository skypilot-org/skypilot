import sky

task = sky.Task(run='nvidia-smi')

if False:
    task.set_resources({
        sky.Resources(sky.AWS(),
                      zone='us-east-1d',
                      use_spot=True,
                      accelerators={'K80': 1}),
        sky.Resources(sky.GCP(), accelerators={'K80': 1}),
    })

if False:
    task.set_resources([
        sky.Resources(sky.GCP(), accelerators={'T4': 1}),
        sky.Resources(sky.GCP(), accelerators={'V100': 1}, use_spot=True),
        sky.Resources(sky.GCP(), accelerators={'V100': 1}),
        sky.Resources(sky.Azure(), accelerators={'K80': 1}),
        sky.Resources(sky.Azure(), accelerators={'T4': 4}),
    ],
                       is_resources_ordered=True)

    sky.launch(task,
               cluster_name=f'my-cluster',
               stream_logs=False,
               down=False,
               dryrun=True)

if True:
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

if False:
    task.set_resources({
        sky.Resources(sky.GCP(), accelerators={'T4': 1}),
        sky.Resources(sky.GCP(), accelerators={'V100': 1}, use_spot=True),
        sky.Resources(sky.GCP(), accelerators={'V100': 1}),
        sky.Resources(sky.Azure(), accelerators={'K80': 1}),
    })

    sky.launch(task,
               cluster_name=f'my-cluster',
               stream_logs=False,
               down=False,
               dryrun=True)

if False:
    task.set_resources({
        sky.Resources(sky.GCP(), accelerators={'V100': 1}, use_spot=True),
        sky.Resources(sky.GCP()),
    })

    sky.launch(task,
               cluster_name=f'my-cluster',
               stream_logs=False,
               down=False,
               dryrun=True)
