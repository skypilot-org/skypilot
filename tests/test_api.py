import apex


def test_sky_launch(enable_all_clouds):
    task = apex.Task()
    job_id, handle = apex.launch(task, dryrun=True)
    assert job_id is None and handle is None
