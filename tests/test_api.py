import sky
from sky.clouds.cloud import Cloud


def test_sky_launch(enable_all_clouds):
    task = sky.Task()
    job_id, handle = sky.get(sky.launch(task, dryrun=True))
    assert job_id is None and handle is None


def test_k8s_alias(monkeypatch, enable_all_clouds):

    def dryrun_task_with_cloud(cloud: Cloud):
        task = sky.Task()
        task.set_resources_override({'cloud': cloud})
        sky.stream_and_get(sky.launch(task, dryrun=True))

    monkeypatch.setattr('sky.provision.kubernetes.utils.get_spot_label',
                        lambda *_, **__: [None, None])
    dryrun_task_with_cloud(sky.K8s())

    dryrun_task_with_cloud(sky.Kubernetes())
