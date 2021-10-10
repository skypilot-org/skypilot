"""Execution layer: resource provisioner + task launcher.

Usage:

   >> planned_dag = sky.Optimizer.optimize(dag)
   >> sky.execute(planned_dag)

Current resource privisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
import subprocess
import textwrap
import typing

import jinja2
from mako import template

import sky

# NOTE: keep in sync with the cluster template 'file_mounts'.
SKY_REMOTE_WORKDIR = '/tmp/workdir'

_CLOUD_TO_TEMPLATE = {
    sky.clouds.AWS: 'config/aws.yml.j2',
}


def _get_cluster_config_template(task):
    cloud = task.best_resources.cloud
    return _CLOUD_TO_TEMPLATE[type(cloud)]


def _fill_template(template_path: str,
                   variables: dict,
                   output_path: typing.Optional[str] = None) -> str:
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
    print(f'Created or updated file {output_path}')
    return output_path


def _write_cluster_config(task, cluster_config_template):
    return _fill_template(
        cluster_config_template,
        {
            'instance_type': task.best_resources.types,
            'workdir': task.workdir,
            'setup_command': task.setup_command,
        },
    )


def _run(cmd, **kwargs) -> subprocess.CompletedProcess:
    print('$ ' + cmd)
    ret = subprocess.run(cmd, shell=True, **kwargs)
    ret.check_returncode()
    return ret


def execute(dag: sky.Dag, teardown=False):
    assert len(dag) == 1, 'Job launcher assumes 1 task for now'
    assert not teardown, 'Implement by copying from main.py'
    task = dag.tasks[0]
    cluster_config_file = _write_cluster_config(
        task, _get_cluster_config_template(task))

    # Provision resources.
    provision_template = template.Template(
        'ray up -y ${cluster_config_file} --no-config-cache')
    provision_cmd = provision_template.render(
        cluster_config_file=cluster_config_file)
    _run(provision_cmd)

    # Resync file mounts.  Needed if we add a flag to skip the ray up step.
    remote_workdir = SKY_REMOTE_WORKDIR
    sync_template = template.Template('ray rsync_up ${cluster_config_file} \
        ${local_workdir}/ ${remote_workdir}')
    sync_cmd = sync_template.render(cluster_config_file=cluster_config_file,
                                    local_workdir=task.workdir,
                                    remote_workdir=remote_workdir)
    _run(sync_cmd)

    # Execute.
    execute_template = template.Template(
        'ray exec ${cluster_config_file} "cd ${remote_workdir}; ${command}"')
    execute_template = template.Template(
        textwrap.dedent("""\
          ray exec ${cluster_config_file} \
          "cd ${remote_workdir} && ${command}"
    """).strip())
    execute_cmd = execute_template.render(
        cluster_config_file=cluster_config_file,
        remote_workdir=remote_workdir,
        command=task.command,
        setup_command=task.setup_command or ':',
    )
    _run(execute_cmd)
