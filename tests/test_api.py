import sky


def test_sky_launch():
    task = sky.Task()
    job_id, handle = sky.launch(task, dryrun=True)
    assert job_id is None and handle is None
