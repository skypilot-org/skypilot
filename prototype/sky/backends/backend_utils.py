"""Util constants/functions for the backends."""
import datetime
import io
import os
import pathlib
import selectors
import subprocess
import sys
import tempfile
import textwrap
import time
from typing import Dict, List, Optional, Union, Set
import yaml
import zlib

import jinja2

import sky
from sky import authentication as auth
from sky import clouds
from sky import logging
from sky import resources
from sky import task as task_lib

logger = logging.init_logger(__name__)

# An application.  These are the task types to support.
App = Union[task_lib.Task, task_lib.ParTask]
RunId = str
Resources = resources.Resources

# NOTE: keep in sync with the cluster template 'file_mounts'.
SKY_REMOTE_WORKDIR = '/tmp/workdir'
SKY_REMOTE_APP_DIR = '/tmp/sky_app'
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

    def to_absolute(path):
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(sky.__root_dir__), path)
        return path

    template_path = to_absolute(template_path)
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
    output_path = to_absolute(output_path)
    with open(output_path, 'w') as fout:
        fout.write(content)
    return output_path


class FileMountHelper(object):
    """Helper for handling file mounts."""

    @classmethod
    def wrap_file_mount(cls, path: str) -> str:
        """Prepends ~/<opaque dir>/ to a path to work around permission issues.

        Examples:
        /root/hello.txt -> ~/<opaque dir>/root/hello.txt
        local.txt -> ~/<opaque dir>/local.txt

        After the path is synced, we can later create a symlink to this wrapped
        path from the original path, e.g., in the initialization_commands of the
        ray autoscaler YAML.
        """
        return os.path.join(_SKY_REMOTE_FILE_MOUNTS_DIR, path.lstrip('/'))

    @classmethod
    def make_safe_symlink_command(
            cls,
            *,
            source: str,
            target: str,
            download_target_commands: Optional[List[str]] = None) -> str:
        """Returns a command that safely symlinks 'source' to 'target'.

        All intermediate directories of 'source' will be owned by $USER,
        excluding the root directory (/).

        'source' must be an absolute path; both 'source' and 'target' must not
        end with a slash (/).

        This function is needed because a simple 'ln -s target source' may
        fail: 'source' can have multiple levels (/a/b/c), its parent dirs may
        or may not exist, can end with a slash, or may need sudo access, etc.

        Cases of <target: local> file mounts and their behaviors:

            /existing_dir: ~/local/dir
              - error out saying this cannot be done as LHS already exists
            /existing_file: ~/local/file
              - error out saying this cannot be done as LHS already exists
            /existing_symlink: ~/local/file
              - overwrite the existing symlink; this is important because sky
                run can be run multiple times
            Paths that start with ~/ and /tmp/ do not have the above
            restrictions; just delegate to rsync behaviors.
        """
        assert os.path.isabs(source), source
        assert not source.endswith('/') and not target.endswith('/'), (source,
                                                                       target)
        # Below, use sudo in case the symlink needs sudo access to create.
        # Prepare to create the symlink:
        #  1. make sure its dir(s) exist & are owned by $USER.
        dir_of_symlink = os.path.dirname(source)
        commands = [
            # mkdir, then loop over '/a/b/c' as /a, /a/b, /a/b/c.  For each,
            # chown $USER on it so user can use these intermediate dirs
            # (excluding /).
            f'sudo mkdir -p {dir_of_symlink}',
            # p: path so far
            ('(p=""; '
             f'for w in $(echo {dir_of_symlink} | tr "/" " "); do '
             'p=${p}/${w}; sudo chown $USER $p; done)')
        ]
        #  2. remove any existing symlink (ln -f may throw 'cannot
        #     overwrite directory', if the link exists and points to a
        #     directory).
        commands += [
            # Error out if source is an existing, non-symlink directory/file.
            f'((test -L {source} && sudo rm {source} &>/dev/null) || '
            f'(test ! -e {source} || '
            f'(echo "!!! Failed mounting because path exists ({source})"; '
            'exit 1)))',
        ]
        if download_target_commands is not None:
            commands += download_target_commands
        commands += [
            # Link.
            f'sudo ln -s {target} {source}',
            # chown.  -h to affect symlinks only.
            f'sudo chown -h $USER {source}',
        ]
        return ' && '.join(commands)


class SSHConfigHelper(object):
    """Helper for handling local SSH configuration."""

    ssh_conf_path = '~/.ssh/config'

    @classmethod
    def _get_generated_config(cls, autogen_comment: str, host_name: str,
                              ip: str, username: str, ssh_key_path: str):
        codegen = textwrap.dedent(f"""\
            {autogen_comment}
            Host {host_name}
              HostName {ip}
              User {username}
              IdentityFile {ssh_key_path}
              IdentitiesOnly yes
              ForwardAgent yes
              Port 22
              """)
        return codegen

    @classmethod
    def add_cluster(cls, cluster_name: str, ip: str,
                    auth_config: Dict[str, str]):
        """Add authentication information for cluster to local SSH config file.

        If a host with `cluster_name` already exists and the configuration was
        not added by sky, then `ip` is used to identify the host instead in the
        file.

        If a host with `cluster_name` already exists and the configuration was
        added by sky (e.g. a spot instance), then the configuration is
        overwritten.

        Args:
            cluster_name: Cluster name (see `sky status`)
            ip: IP address of head node associated with the cluster
            auth_config: read_yaml(handle.cluster_yaml)['auth']
        """
        username = auth_config['ssh_user']
        key_path = os.path.expanduser(auth_config['ssh_private_key'])
        host_name = cluster_name
        sky_autogen_comment = '# Added by sky (use `sky stop/down ' + \
                            f'{cluster_name}` to remove)'
        overwrite = False
        overwrite_begin_idx = None

        config_path = os.path.expanduser(cls.ssh_conf_path)
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = f.readlines()

            # If an existing config with `cluster_name` exists, raise a warning.
            for i, line in enumerate(config):
                if line.strip() == f'Host {cluster_name}':
                    prev_line = config[i - 1] if i - 1 > 0 else ''
                    if prev_line.strip().startswith(sky_autogen_comment):
                        overwrite = True
                        overwrite_begin_idx = i - 1
                    else:
                        logger.warning(f'{cls.ssh_conf_path} contains '
                                       f'host named {cluster_name}.')
                        host_name = ip
                        logger.warning(f'Using {ip} to identify host instead.')
                    break

        codegen = cls._get_generated_config(sky_autogen_comment, host_name, ip,
                                            username, key_path)

        # Add (or overwrite) the new config.
        if overwrite:
            assert overwrite_begin_idx is not None
            updated_lines = codegen.splitlines(keepends=True)
            config[overwrite_begin_idx:overwrite_begin_idx +
                   len(updated_lines)] = updated_lines
            with open(config_path, 'w') as f:
                f.write('\n')
                f.writelines(config)
                f.write('\n')
        else:
            with open(config_path, 'a') as f:
                f.write('\n')
                f.write(codegen)

    @classmethod
    def remove_cluster(cls, ip: str, auth_config: Dict[str, str]):
        """Remove authentication information for cluster from local SSH config.

        If no existing host matching the provided specification is found, then
        nothing is removed.

        Args:
            ip: IP address of a cluster's head node.
            auth_config: read_yaml(handle.cluster_yaml)['auth']
        """
        username = auth_config['ssh_user']

        config_path = os.path.expanduser(cls.ssh_conf_path)
        with open(config_path) as f:
            config = f.readlines()

        # Scan the config for the cluster name.
        start_line_idx = None
        for i, line in enumerate(config):
            next_line = config[i + 1] if i + 1 < len(config) else ''
            if line.strip() == f'HostName {ip}' and next_line.strip(
            ) == f'User {username}':
                start_line_idx = i - 1
                break

        if start_line_idx is None:  # No config to remove.
            return

        # Scan for end of the cluster config.
        end_line_idx = None
        cursor = start_line_idx + 1
        start_line_idx -= 1  # remove auto-generated comment
        while cursor < len(config):
            if config[cursor].strip().startswith(
                    '# ') or config[cursor].strip().startswith('Host '):
                end_line_idx = cursor
                break
            cursor += 1

        # Remove sky-generated config and update the file.
        config[start_line_idx:end_line_idx] = []
        with open(config_path, 'w') as f:
            f.writelines(config)


# TODO: too many things happening here - leaky abstraction. Refactor.
def write_cluster_config(run_id: RunId,
                         task: task_lib.Task,
                         cluster_config_template: str,
                         cluster_name: str,
                         region: Optional[clouds.Region] = None,
                         zones: Optional[List[clouds.Zone]] = None,
                         dryrun: bool = False):
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
        assert isinstance(
            cloud, clouds.Azure
        ) or zones is not None, 'Set either both or neither for: region, zones.'
    region = region.name
    if isinstance(cloud, clouds.AWS):
        # Only AWS supports multiple zones in the 'availability_zone' field.
        zones = [zone.name for zone in zones]
    elif isinstance(cloud, clouds.Azure):
        # Azure does not support specific zones.
        zones = []
    else:
        zones = [zones[0].name]

    aws_default_ami = None
    if isinstance(cloud, clouds.AWS):
        aws_default_ami = cloud.get_default_ami(region)

    assert cluster_name is not None

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

    # File mounts handling for remote paths possibly without write access:
    #  (1) in 'file_mounts' sections, add <prefix> to these target paths.
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
            if not os.path.isabs(remote) and not remote.startswith('~/'):
                remote = f'~/{remote}'
            if remote.startswith('~/') or remote.startswith('/tmp/'):
                # Skip as these should be writable locations.
                wrapped_file_mounts[remote] = local
                continue
            assert os.path.isabs(remote), (remote, local)
            wrapped_remote = FileMountHelper.wrap_file_mount(remote)
            wrapped_file_mounts[wrapped_remote] = local
            command = FileMountHelper.make_safe_symlink_command(
                source=remote, target=wrapped_remote)
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
    dump_yaml(cluster_config_file, config)


def read_yaml(path):
    with open(path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def dump_yaml(path, config):
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
                                 num_nodes: int) -> bool:
    """Returns whether the entire ray cluster is ready."""
    # FIXME: It may takes a while for the cluster to be available for ray,
    # especially for Azure, causing `ray exec` to fail.
    if num_nodes <= 1:
        return
    expected_worker_count = num_nodes - 1
    if isinstance(cloud, (clouds.AWS, clouds.Azure)):
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
        if '(no pending nodes)' in output and '(no failures)' in output:
            # Bug in ray autoscaler: e.g., on GCP, if requesting 2 nodes that
            # GCP can satisfy only by half, the worker node would be forgotten.
            # The correct behavior should be for it to error out.
            return False  # failed
        time.sleep(10)
    return True  # success


def run_command_on_ip_via_ssh(ip: str,
                              command: str,
                              private_key: str,
                              container_name: Optional[str],
                              ssh_user: str = 'ubuntu') -> None:
    if container_name is not None:
        command = command.replace('\\', '\\\\').replace('"', '\\"')
        command = f'docker exec {container_name} /bin/bash -c "{command}"'
    cmd = [
        'ssh',
        '-i',
        private_key,
        '-o',
        'StrictHostKeyChecking=no',
        '{}@{}'.format(ssh_user, ip),
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
            events = sel.select()
            for key, _ in events:
                line = key.fileobj.readline()
                if not line:
                    # Unregister the io when EOF reached
                    sel.unregister(key.fileobj)
                    continue
                # Remove special characters to avoid cursor hidding
                line = line.replace('\x1b[?25l', '')
                if start_streaming_at in line:
                    start_streaming_flag = True
                if key.fileobj is out_io:
                    stdout += line
                    out_stream = sys.stdout
                else:
                    stderr += line
                    out_stream = sys.stderr
                if stream_logs and start_streaming_flag:
                    out_stream.write(line)
                    out_stream.flush()
                fout.write(line)
    return stdout, stderr


def run(cmd, **kwargs):
    shell = kwargs.pop('shell', True)
    check = kwargs.pop('check', True)
    executable = kwargs.pop('executable', '/bin/bash')
    if not shell:
        executable = None
    return subprocess.run(cmd,
                          shell=shell,
                          check=check,
                          executable=executable,
                          **kwargs)


def run_no_outputs(cmd, **kwargs):
    return run(cmd,
               stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL,
               **kwargs)


def run_with_log(cmd: List[str],
                 log_path: str,
                 stream_logs: bool = False,
                 start_streaming_at: str = '',
                 return_none: bool = False,
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
        if return_none:
            return None
        return proc, stdout, stderr


def check_local_gpus() -> bool:
    """Returns whether GPUs are available on the local machine by checking
    if nvidia-smi is installed.

    Returns True if nvidia-smi is installed, false if not.
    """
    p = subprocess.run(['which', 'nvidia-smi'],
                       capture_output=True,
                       check=False)
    return p.returncode == 0


def is_same_requested_resources(r1: Set[Resources], r2: Set[Resources]):
    """Returns whether the requested resources are the same.

    Args:
        r1: Set of Resources requested previously.
        r2: Set of Resources newly requested.

    Returns:
        True if the resources are the same, false otherwise.
    """
    assert len(r1) == 1 and len(r2) == 1
    r1 = list(r1)[0]
    r2 = list(r2)[0]
    return r1.is_same_resources(r2)


def run_bash_command_with_log(bash_command: str,
                              log_path: str,
                              stream_logs: bool = False):
    with tempfile.NamedTemporaryFile('w', prefix='sky_app_') as fp:
        fp.write(bash_command)
        fp.flush()
        script_path = fp.name
        run_with_log(
            f'/bin/bash {script_path}',
            log_path,
            stream_logs=stream_logs,
            return_none=True,
            # The script will be not found without this
            shell=True,
        )


def make_task_bash_script(codegen: str) -> str:
    script = [
        textwrap.dedent(f"""\
                #!/bin/bash
                . {SKY_REMOTE_APP_DIR}/sky_env_var.sh 2> /dev/null || true
                . $(conda info --base)/etc/profile.d/conda.sh 2> /dev/null || true
                cd {SKY_REMOTE_WORKDIR}"""),
        codegen,
    ]
    script = '\n'.join(script)
    return script
