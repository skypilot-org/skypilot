"""Util constants/functions for the backends."""
import datetime
from typing import Optional, Union

import jinja2

from sky import logging
from sky import task

logging = logging.init_logger(__name__)

# An application.  These are the task types to support.
App = Union[task.Task, task.ParTask]
RunId = str
# NOTE: keep in sync with the cluster template 'file_mounts'.
SKY_REMOTE_WORKDIR = '/tmp/workdir'


def _fill_template(template_path: str,
                   variables: dict,
                   output_path: Optional[str] = None) -> str:
    """Create a file from a Jinja template and return the filename."""
    assert template_path.endswith('.j2'), template_path
    with open(template_path) as fin:
        template = fin.read()
    template = jinja2.Template(template)
    content = template.render(**variables)
    if output_path is None:
        output_path, _ = template_path.rsplit('.', 1)
    with open(output_path, 'w') as fout:
        fout.write(content)
    logging.info(f'Created or updated file {output_path}')
    return output_path


def write_cluster_config(run_id: RunId, task, cluster_config_template: str):
    """Returns {provisioner: path to yaml, the provisioning spec}.

    'provisioner' can be
      - 'ray'
      - 'gcloud' (if TPU is requested)
    """
    cloud = task.best_resources.cloud
    resources_vars = cloud.make_deploy_resources_variables(task)
    config_dict = {}

    config_dict['ray'] = _fill_template(
        cluster_config_template,
        dict(
            resources_vars, **{
                'run_id': run_id,
                'setup_command': task.setup,
                'workdir': task.workdir,
                'docker_image': task.docker_image,
                'container_name': task.container_name,
                'num_nodes': task.num_nodes,
                'file_mounts': task.get_local_to_remote_file_mounts() or {},
                'max_nodes': task.max_nodes,
            }))
    if resources_vars.get('tpu_type') is not None:
        # FIXME: replace hard-coding paths
        config_dict['gcloud'] = (_fill_template('config/gcp-tpu-create.sh.j2',
                                                dict(resources_vars)),
                                 _fill_template('config/gcp-tpu-delete.sh.j2',
                                                dict(resources_vars)))
    return config_dict


def get_run_id() -> RunId:
    return 'sky-' + datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S-%f')
