from typing import Optional

import fastapi
import pydantic
import yaml

import sky
from sky import execution
from sky import optimizer
from sky.api import sdk

app = fastapi.FastAPI(root_path='/api/v1', debug=True)


class LaunchBody(pydantic.BaseModel):
    task: str
    cluster_name: Optional[str] = None
    retry_until_up: bool = False
    idle_minutes_to_autostop: Optional[int] = None
    dryrun: bool = False
    down: bool = False
    optimize_target: optimizer.OptimizeTarget = optimizer.OptimizeTarget.COST
    detach_setup: bool = False
    no_setup: bool = False
    clone_disk_from: Optional[str] = None
    # Internal only:
    # pylint: disable=invalid-name
    _is_launched_by_spot_controller: bool = False


@app.post('/launch')
async def launch(
    launch_body: LaunchBody,
    background_tasks: fastapi.BackgroundTasks = None,
):
    """Launch a task.

    Args:
        task: The YAML string of the task to launch.
    """
    task_config = yaml.safe_load(launch_body.task)
    task_ = sky.Task.from_yaml_config(task_config)

    assert background_tasks is not None
    background_tasks.add_task(
        execution.launch,
        task=task_,
        cluster_name=launch_body.cluster_name,
        retry_until_up=launch_body.retry_until_up,
        idle_minutes_to_autostop=launch_body.idle_minutes_to_autostop,
        dryrun=launch_body.dryrun,
        down=launch_body.down,
        optimize_target=launch_body.optimize_target,
        detach_setup=launch_body.detach_setup,
        detach_run=True,
        no_setup=launch_body.no_setup,
        clone_disk_from=launch_body.clone_disk_from,
        _is_launched_by_spot_controller=launch_body.
        _is_launched_by_spot_controller,
    )


app.include_router(sdk.app_router)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
