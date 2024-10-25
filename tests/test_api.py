import sky
from sky.clouds.cloud import Cloud


def test_sky_launch(enable_all_clouds):
    task = sky.Task()
    job_id, handle = sky.launch(task, dryrun=True)
    assert job_id is None and handle is None


def test_k8s_alias(enable_all_clouds):

    def dryrun_task_with_cloud(cloud: Cloud):
        task = sky.Task()
        task.set_resources_override({'cloud': cloud})
        sky.launch(task, dryrun=True)

    dryrun_task_with_cloud(sky.K8s())

    dryrun_task_with_cloud(sky.Kubernetes())
