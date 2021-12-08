"""Util constants/functions for the backends."""
import datetime
import getpass
import io
import os
import pathlib
import selectors
import subprocess
import tempfile
import textwrap
import time
from typing import Dict, List, Optional, Union
import uuid
import yaml
import zlib

import jinja2

import sky
from sky import authentication as auth
from sky import clouds
from sky import logging
from sky import task as task_lib

logger = logging.init_logger(__name__)

# An application.  These are the task types to support.
App = Union[task_lib.Task, task_lib.ParTask]
RunId = str
# NOTE: keep in sync with the cluster template 'file_mounts'.
SKY_REMOTE_WORKDIR = '/tmp/workdir'
IP_ADDR_REGEX = r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'
SKY_LOGS_DIRECTORY = './sky_logs'

# Do not use /tmp because it gets cleared on VM restart.
_SKY_REMOTE_FILE_MOUNTS_DIR = '~/.sky/file_mounts/'


def get_rel_path(path: str) -> str:
    cwd = os.getcwd()
    common = os.path.commonpath([path, cwd])
    return os.path.relpath(path, common)


def _fill_template(template_path: str,
                   variables: Dict,
                   output_path: Optional[str] = None) -> str:
    """Create a file from a Jinja template and return the filename."""
    assert template_path.endswith('.j2'), template_path
    with open(template_path) as fin:
        template = fin.read()
    template = jinja2.Template(template)
    content = template.render(**variables)
    if output_path is None:
        assert 'cluster_name' in variables, 'cluster_name is required.'
        cluster_name = variables['cluster_name']
        output_path = pathlib.Path(
            template_path).parents[0] / 'user' / f'{cluster_name}.yml'
        os.makedirs(output_path.parents[0], exist_ok=True)
        output_path = str(output_path)
    with open(output_path, 'w') as fout:
        fout.write(content)
    if not os.path.isabs(output_path):
        # Need abs path here, otherwise get_rel_path() below fails.
        output_path = os.path.join(os.path.dirname(sky.__root_dir__),
                                   output_path)
    logger.info(f'Created or updated file {get_rel_path(output_path)}')
    return output_path


def wrap_file_mount(path: str) -> str:
    """Prepends ~/<opaque dir>/ to a path to work around permission issues.

    Examples:
      /root/hello.txt -> ~/<opaque dir>/root/hello.txt
      local.txt -> ~/<opaque dir>/local.txt

    After the path is synced, we can later create a symlink to this wrapped
    path from the original path, e.g., in the initialization_commands of the
    ray autoscaler YAML.
    """
    return os.path.join(_SKY_REMOTE_FILE_MOUNTS_DIR, path.lstrip('/'))


# TODO: too many things happening here - leaky abstraction. Refactor.
def write_cluster_config(run_id: RunId,
                         task: task_lib.Task,
                         cluster_config_template: str,
                         region: Optional[clouds.Region] = None,
                         zones: Optional[List[clouds.Zone]] = None,
                         dryrun: bool = False,
                         cluster_name: Optional[str] = None):
    """Fills in cluster configuration templates and writes them out.

    Returns: {provisioner: path to yaml, the provisioning spec}.
      'provisioner' can be
        - 'ray'
        - 'tpu-create-script' (if TPU is requested)
        - 'tpu-delete-script' (if TPU is requested)
    """
    cloud = task.best_resources.cloud
    resources_vars = cloud.make_deploy_resources_variables(task)
    config_dict = {}

    if region is None:
        assert zones is None, 'Set either both or neither for: region, zones.'
        region = cloud.get_default_region()
        zones = region.zones
    else:
        assert zones is not None, \
            'Set either both or neither for: region, zones.'
    region = region.name
    if isinstance(cloud, clouds.AWS):
        # Only AWS supports multiple zones in the 'availability_zone' field.
        zones = [zone.name for zone in zones]
    else:
        zones = [zones[0].name]

    aws_default_ami = None
    if isinstance(cloud, clouds.AWS):
        aws_default_ami = cloud.get_default_ami(region)

    if cluster_name is None:
        # TODO: change this ID formatting to something more pleasant.
        # User name is helpful in non-isolated accounts, e.g., GCP, Azure.
        cluster_name = f'sky-{uuid.uuid4().hex[:4]}-{getpass.getuser()}'

    setup_sh_path = None
    if task.setup is not None:
        codegen = textwrap.dedent(f"""#!/bin/bash
            . $(conda info --base)/etc/profile.d/conda.sh
            {task.setup}
        """)
        # Use a stable path, /<tempdir>/sky_setup_<checksum>.sh, because
        # rerunning the same task without any changes to the content of the
        # setup command should skip the setup step.  Using NamedTemporaryFile()
        # would generate a random path every time, hence re-triggering setup.
        checksum = zlib.crc32(codegen.encode())
        tempdir = tempfile.gettempdir()
        # TODO: file lock on this path, in case tasks have the same setup cmd.
        with open(os.path.join(tempdir, f'sky_setup_{checksum}.sh'), 'w') as f:
            f.write(codegen)
        setup_sh_path = f.name

    # File mounts handling:
    #  (1) in 'file_mounts' sections, add <prefix> to all target paths.
    #  (2) then, create symlinks from '/.../file' to '<prefix>/.../file'.
    # We need to do these since as of Ray 1.8, this is not supported natively
    # (Docker works though, of course):
    #  https://github.com/ray-project/ray/pull/9332
    #  https://github.com/ray-project/ray/issues/9326
    mounts = task.get_local_to_remote_file_mounts()
    wrapped_file_mounts = {}
    initialization_commands = []
    if mounts is not None:
        for remote, local in mounts.items():
            wrapped_remote = wrap_file_mount(remote)
            wrapped_file_mounts[wrapped_remote] = local
            # Point 'remote' (may or may not exist) to 'wrapped_remote'
            # (guaranteed to exist by ray autoscaler's use of rsync).
            #
            # 'remote' can have multiple levels:
            #   - relative paths, a/b/c
            #   - absolute paths, /a/b/c
            symlink_to_make = remote.rstrip('/')
            dir_of_symlink = os.path.dirname(remote)
            # Below, use sudo in case the symlink needs sudo access to create.
            command = ' && '.join([
                # Prepare to create the symlink:
                #  1. make sure its dir exists.
                f'sudo mkdir -p {dir_of_symlink}',
                #  2. remove any existing symlink (ln -f may throw 'cannot
                #     overwrite directory', if the link exists and points to a
                #     directory).
                f'(sudo rm {symlink_to_make} &>/dev/null || true)',
                # Link.
                f'sudo ln -s {wrapped_remote} {symlink_to_make}',
                # chown.
                f'sudo chown $USER {symlink_to_make}',
            ])
            initialization_commands.append(command)

    yaml_path = _fill_template(
        cluster_config_template,
        dict(
            resources_vars,
            **{
                'cluster_name': cluster_name,
                'run_id': run_id,
                'setup_sh_path': setup_sh_path,
                'workdir': task.workdir,
                'docker_image': task.docker_image,
                'container_name': task.container_name,
                'num_nodes': task.num_nodes,
                # File mounts handling.
                'file_mounts': wrapped_file_mounts,
                'initialization_commands': initialization_commands or None,
                # Region/zones.
                'region': region,
                'zones': ','.join(zones),
                # AWS only.
                'aws_default_ami': aws_default_ami,
            }))
    config_dict['cluster_name'] = cluster_name
    config_dict['ray'] = yaml_path
    if dryrun:
        return config_dict
    _add_ssh_to_cluster_config(cloud, yaml_path)
    if resources_vars.get('tpu_type') is not None:
        scripts = tuple(
            _fill_template(
                path,
                dict(resources_vars, **{
                    'zones': ','.join(zones),
                }),
                # Use new names for TPU scripts so that different runs can use
                # different TPUs.  Put in config/user/ to be consistent with
                # cluster yamls.
                output_path=path.replace('.sh.j2', f'.{cluster_name}.sh').
                replace('config/', 'config/user/'),
            ) for path in
            ['config/gcp-tpu-create.sh.j2', 'config/gcp-tpu-delete.sh.j2'])
        config_dict['tpu-create-script'] = scripts[0]
        config_dict['tpu-delete-script'] = scripts[1]
    return config_dict


def _add_ssh_to_cluster_config(cloud_type, cluster_config_file):
    """Adds SSH key info to the cluster config.

    This function's output removes comments included in the jinja2 template.
    """
    with open(cluster_config_file, 'r') as f:
        config = yaml.safe_load(f)
    cloud_type = str(cloud_type)
    if cloud_type == 'AWS':
        config = auth.setup_aws_authentication(config)
    elif cloud_type == 'GCP':
        config = auth.setup_gcp_authentication(config)
    elif cloud_type == 'Azure':
        config = auth.setup_azure_authentication(config)
    else:
        raise ValueError('Cloud type not supported, must be [AWS, GCP, Azure]')
    yaml_dump(cluster_config_file, config)


def yaml_dump(path, config):
    # https://github.com/yaml/pyyaml/issues/127
    class LineBreakDumper(yaml.SafeDumper):

        def write_line_break(self, data=None):
            super().write_line_break(data)
            if len(self.indents) == 1:
                super().write_line_break()

    with open(path, 'w') as f:
        yaml.dump(config,
                  f,
                  Dumper=LineBreakDumper,
                  sort_keys=False,
                  default_flow_style=False)


def get_run_id() -> RunId:
    return 'sky-' + datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S-%f')


def wait_until_ray_cluster_ready(cloud: clouds.Cloud, cluster_config_file: str,
                                 num_nodes: int):
    if num_nodes <= 1:
        return
    expected_worker_count = num_nodes - 1
    if isinstance(cloud, clouds.AWS):
        worker_str = 'ray.worker.default'
    elif isinstance(cloud, clouds.GCP):
        worker_str = 'ray_worker_default'
    else:
        assert False, f'No support for distributed clusters for {cloud}.'
    while True:
        proc = subprocess.run(f'ray exec {cluster_config_file} "ray status"',
                              shell=True,
                              check=True,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        output = proc.stdout.decode('ascii')
        logger.info(output)
        if f'{expected_worker_count} {worker_str}' in output:
            break
        time.sleep(10)


def run_command_on_ip_via_ssh(ip: str,
                              command: str,
                              private_key: str,
                              container_name: Optional[str],
                              user: str = 'ubuntu') -> None:
    if container_name is not None:
        command = command.replace('\\', '\\\\').replace('"', '\\"')
        command = f'docker exec {container_name} /bin/bash -c "{command}"'
    cmd = [
        'ssh',
        '-i',
        private_key,
        '-o',
        'StrictHostKeyChecking=no',
        '{}@{}'.format(user, ip),
        command  # TODO: shlex.quote() doesn't work.  Is it needed in a list?
    ]
    with subprocess.Popen(cmd,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          universal_newlines=True) as proc:
        outs, errs = proc.communicate()
        if outs:
            logger.info(outs)
        if proc.returncode:
            if errs:
                logger.error(errs)
            raise subprocess.CalledProcessError(proc.returncode, cmd)


def redirect_process_output(proc, log_path, stream_logs, start_streaming_at=''):
    """Redirect the process's filtered stdout/stderr to both stream and file"""
    dirname = os.path.dirname(log_path)
    os.makedirs(dirname, exist_ok=True)

    out_io = io.TextIOWrapper(proc.stdout,
                              encoding='utf-8',
                              newline='',
                              errors='replace')
    err_io = io.TextIOWrapper(proc.stderr,
                              encoding='utf-8',
                              newline='',
                              errors='replace')
    sel = selectors.DefaultSelector()
    sel.register(out_io, selectors.EVENT_READ)
    sel.register(err_io, selectors.EVENT_READ)

    stdout = ''
    stderr = ''

    start_streaming_flag = False
    with open(log_path, 'a') as fout:
        while len(sel.get_map()) > 0:
            for key, _ in sel.select():
                line = key.fileobj.readline()
                if not line:
                    sel.unregister(key.fileobj)
                    break
                if start_streaming_at in line:
                    start_streaming_flag = True
                if key.fileobj is out_io:
                    stdout += line
                    fout.write(line)
                    fout.flush()
                else:
                    stderr += line
                    fout.write(line)
                    fout.flush()
                if stream_logs and start_streaming_flag:
                    print(line, end='')
    return stdout, stderr


def run(cmd, **kwargs):
    shell = kwargs.pop('shell', True)
    check = kwargs.pop('check', True)
    return subprocess.run(cmd,
                          shell=shell,
                          check=check,
                          executable='/bin/bash',
                          **kwargs)


def run_no_outputs(cmd, **kwargs):
    return run(cmd,
               stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL,
               **kwargs)


def run_with_log(cmd,
                 log_path,
                 stream_logs=False,
                 start_streaming_at='',
                 **kwargs):
    """Runs a command and logs its output to a file.

    Retruns the process, stdout and stderr of the command.
      Note that the stdout and stderr is already decoded.
    """
    with subprocess.Popen(cmd,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          **kwargs) as proc:
        stdout, stderr = redirect_process_output(
            proc, log_path, stream_logs, start_streaming_at=start_streaming_at)
        proc.wait()
        return proc, stdout, stderr
