import sky

file_mounts = {
    # '/remote': '/local'
}
task1 = sky.Task(
    'task1',
    setup="echo 'setup for task1 (head)'",
    run="echo 'run for task1 (head)'",
)
task1.set_file_mounts(file_mounts)
task1.set_resources({
    sky.Resources(sky.GCP(), 'n1-standard-4', 'T4'),
})

task2 = sky.Task(
    'task2',
    setup="echo 'setup for task2 (worker)'",
    run="echo 'run for task2 (worker)'",
    num_nodes=2,
)
task2.set_resources({
    sky.Resources(sky.GCP(), 'n1-standard-8', 'V100'),
})

sky.launch(sky.TaskGroup([task1, task2]), dryrun=False)
