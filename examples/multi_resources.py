import sky

task = sky.Task(run='nvidia-smi')

# task.set_resources({
#     sky.Resources(sky.AWS(),
#                   zone='us-east-1d',
#                   use_spot=True,
#                   accelerators={'K80': 1}),
#     sky.Resources(sky.GCP(), accelerators={'K80': 1}),
# })

task.set_resources([
    sky.Resources(accelerators={'V100': 1}),
    sky.Resources(accelerators={'K80': 1}),
])

sky.launch(task,
           cluster_name=f'my-cluster',
           stream_logs=False,
           down=False,
           dryrun=True)
#sky.exec(task, cluster_name='my-cluster', stream_logs=True)
