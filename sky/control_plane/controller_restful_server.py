from typing import Union

import fastapi
import pydantic

from sky.control_plane import utils


class JobConfig(pydantic.BaseModel):
    yaml_config: str


app = fastapi.FastAPI()

global_job_id = utils.FilelockMonotonicID('/tmp/sky_job_id.lock')


@app.get('/')
def read_root():
    return {'Hello': 'World'}


@app.get('/items/{item_id}')
def read_item(item_id: int, q: Union[str, None] = None):
    return {'item_id': item_id, 'q': q}


@app.post('/reset_job_id')
def reset_job_id():
    global_job_id.reset()
    return 'ok'


@app.post('/launch_job')
def launch_job(cluster_name: str, config: JobConfig):
    print(config)
    return f'JobID = {global_job_id.next_id()}'
