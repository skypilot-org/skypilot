"""Sky backends: for provisioning, setup, scheduling, and execution."""
import json
import os
import shlex
import subprocess
import tempfile
import textwrap
from typing import Any, Callable, Dict, Optional

import colorama

from sky import backend_utils
from sky import clouds
from sky import cloud_stores
from sky import logging
from sky import task as task_mod
from sky import resources

logging = logging.init_logger(__name__)

App = backend_utils.App
Resources = resources.Resources
Path = str
PostSetupFn = Callable[[str], Any]

SKY_REMOTE_WORKDIR = backend_utils.SKY_REMOTE_WORKDIR


def _run(cmd, **kwargs):
    subprocess.run(cmd, shell=True, check=True, **kwargs)


def _get_cluster_config_template(task):
    _CLOUD_TO_TEMPLATE = {
        clouds.AWS: 'config/aws-ray.yml.j2',
        # clouds.Azure: 'config/azure-ray.yml.j2',
        clouds.GCP: 'config/gcp-ray.yml.j2',
    }
    cloud = task.best_resources.cloud
    return _CLOUD_TO_TEMPLATE[type(cloud)]


class Backend(object):
    """Backend interface: handles provisioning, setup, and scheduling."""

    # Backend-specific handle to the launched resources (e.g., a cluster).
    # Examples: 'cluster.yaml'; 'ray://...', 'k8s://...'.
    ResourceHandle = Any

    def provision(self, task: App, to_provision: Resources,
                  dryrun: bool) -> ResourceHandle:
        raise NotImplementedError

    def sync_workdir(self, handle: ResourceHandle, workdir: Path) -> None:
        raise NotImplementedError

    def sync_file_mounts(
            self,
            handle: ResourceHandle,
            all_file_mounts: Dict[Path, Path],
            cloud_to_remote_file_mounts: Optional[Dict[Path, Path]],
    ) -> None:
        raise NotImplementedError

    def run_post_setup(self, handle: ResourceHandle,
                       post_setup_fn: PostSetupFn) -> None:
        raise NotImplementedError

    def execute(self, handle: ResourceHandle, task: App) -> None:
        raise NotImplementedError

    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        """Post execute(): e.g., print helpful inspection messages."""
        raise NotImplementedError

    def teardown(self, handle: ResourceHandle) -> None:
        raise NotImplementedError


class CloudVmRayBackend(Backend):
    """Backend: runs on cloud virtual machines, managed by Ray.

    Changing this class may also require updates to:
      * Cloud providers' templates under config/
      * Cloud providers' implementations under clouds/
    """

    ResourceHandle = str  # yaml file

    def __init__(self):
        # TODO: should include this as part of the handle.
        self._managed_tpu = None

    def provision(self, task: App, to_provision: Resources,
                  dryrun: bool) -> ResourceHandle:
        """Provisions using 'ray up'."""
        # For each node (head or workers)
        #   --resources = {acc: acc_cnt} from task.best_resources
        #                   (cpu/mem etc. set by Ray automatically)
        run_id = backend_utils.get_run_id()
        config_dict = backend_utils.write_cluster_config(
            run_id, task, _get_cluster_config_template(task))
        if dryrun:
            return
        # ray up: the VMs.
        cluster_config_file = config_dict['ray']
        _run(f'ray up -y {cluster_config_file} --no-config-cache')
        # gcloud: TPU.
        tpu_name = to_provision.accelerator_args.get('tpu_name')
        if tpu_name is not None:
            assert 'gcloud' in config_dict, \
                'Expect TPU provisioning with gcloud'
            try:
                _run(f'bash {config_dict["gcloud"][0]}',
                     stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode('ascii')
                if 'ALREADY_EXISTS' in stderr:
                    logging.info(
                        f'TPU {tpu_name} already exists; skipped creation.')
                else:
                    raise e
            _run(
                f"ray exec {cluster_config_file} \'echo \"export TPU_NAME={tpu_name}\" >> ~/.bashrc\'"
            )
            self._managed_tpu = config_dict['gcloud']
        return cluster_config_file

    def sync_workdir(self, handle: ResourceHandle, workdir: Path) -> None:
        # TODO: do we really need this if provision() takes care of it?
        # TODO: this only syncs to head.  -A flag from ray rsync_up is being
        # deprecated.
        _run(f'ray rsync_up {handle} {workdir}/ {SKY_REMOTE_WORKDIR}')

    def sync_file_mounts(
            self,
            handle: ResourceHandle,
            all_file_mounts: Dict[Path, Path],
            cloud_to_remote_file_mounts: Optional[Dict[Path, Path]],
    ) -> None:
        # TODO: this only syncs to head.
        # 'all_file_mounts' should already have been handled in provision()
        # using the yaml file.  Here we handle cloud -> remote file transfers.
        mounts = cloud_to_remote_file_mounts
        if mounts is not None:
            for dst, src in mounts.items():
                storage = cloud_stores.get_storage_from_path(src)
                # TODO: room for improvement.  Here there are many moving parts
                # (download gsutil on remote, run gsutil on remote).  Consider
                # alternatives (smart_open, each provider's own sdk), a
                # data-transfer container etc.  We also assumed 'src' is a
                # directory.
                download_command = storage.make_download_dir_command(
                    source=src, destination=dst)
                _run(f'ray exec {handle} \'{download_command}\'')

    def run_post_setup(self, handle: ResourceHandle,
                       post_setup_fn: PostSetupFn) -> None:
        # ips = 'ray get-head-ip' + 'ray get-worker-ips'
        # cmds_per_ip = task.post_setup_fn(ips)
        # TODO: run this on each node; ssh ip bash -c for ip in ips?
        pass

    def _execute_par_task(self, handle: ResourceHandle,
                          par_task: task_mod.ParTask) -> None:
        # Case: ParTask(tasks), t.num_nodes == 1 for t in tasks
        for t in par_task.tasks:
            assert t.num_nodes == 1, \
                f'ParTask does not support inner Tasks with num_nodes > 1: {t}'
        # Strategy:
        #  ray.init(..., log_to_driver=False); otherwise too many logs.
        #  for task:
        #    submit _run_cmd(cmds[i]) with resource {task i's resource}
        # Concrete impl. of the above: codegen a script that contains all the
        # tasks, rsync the script to head, and run that script on head.

        # We cannot connect from this local node to the remote Ray cluster
        # using a Ray client, because the default port 10001 may not be open to
        # this local node.
        #
        # One downside(?) of client mode is to dictate local machine having the
        # same python & ray versions as the cluster.  We can plumb through the
        # yamls to take care of it.  The upsides are many-fold (e.g., directly
        # manipulating the futures).
        #
        # TODO: possible to open the port in the yaml?  Run Ray inside docker?
        codegen = [
            textwrap.dedent(f"""\
        import ray
        import subprocess
        ray.init('auto', namespace='__sky__')
        futures = []
        """)
        ]
        for i, t in enumerate(par_task.tasks):
            cmd = shlex.quote(f'cd {SKY_REMOTE_WORKDIR} && {t.run}')
            # We can't access t.best_resources because the inner task doesn't
            # undergo optimization.
            resources = par_task.get_task_resource_demands(i)
            if resources is not None:
                resources_str = f', resources={json.dumps(resources)}'
            else:
                resources_str = ''
            name = f'task-{i}'
            task_i_codegen = textwrap.dedent(f"""\
        futures.append(ray.remote(lambda: subprocess.run(
            {cmd}, shell=True, check=True)).options(name='{name}'{resources_str}).remote())
        """)
            codegen.append(task_i_codegen)
        # Block.
        codegen.append('ray.get(futures)\n')
        codegen = '\n'.join(codegen)

        # Write out the generated code.
        with tempfile.NamedTemporaryFile('w', prefix='sky_') as fp:
            fp.write(codegen)
            fp.flush()
            basename = os.path.basename(fp.name)
            # Rather than 'rsync_up' & 'exec', the alternative of 'ray submit'
            # may not work as the remote VM may use system python (python2) to
            # execute the script.  Happens for AWS.
            _run(f'ray rsync_up {handle} {fp.name} /tmp/{basename}')
        # Note the use of python3.
        _run(f'ray exec {handle} \'python3 /tmp/{basename}\'')

    def execute(self, handle: ResourceHandle, task: App) -> None:
        # Execution logic differs for three types of tasks.

        # Case: ParTask(tasks), t.num_nodes == 1 for t in tasks
        if isinstance(task, task_mod.ParTask):
            return self._execute_par_task(handle, task)

        # Otherwise, handle a basic Task.
        if task.run is None:
            logging.info(f'Nothing to run; run command not specified:\n{task}')
            return

        # Case: Task(run, num_nodes=1)
        if task.num_nodes == 1:
            # Launch the command as a Ray task.
            cmd = 'ray exec {} {}'.format(
                handle, shlex.quote(f'cd {SKY_REMOTE_WORKDIR} && {task.run}'))
            _run(cmd)
            return

        # Case: Task(run, num_nodes=N)
        # Hacky solution: for ip: ssh ip bashc -c cmd
        # Ray solution:
        #  - ray.init(..., log_to_driver=False); otherwise too many logs.
        #  - for node:
        #    - submit _run_cmd(cmd) with resource {node_i: 1}
        assert False, 'No support for Task(..., num_nodes=N) for now.'

    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        colorama.init()
        Style = colorama.Style
        if not teardown:
            logging.info(
                f'\nTo log into the head VM:\t{Style.BRIGHT}ray attach {handle} {Style.RESET_ALL}\n'
                f'\nTo teardown the resources:\t{Style.BRIGHT}ray down {handle} -y {Style.RESET_ALL}\n'
            )
            if self._managed_tpu is not None:
                logging.info(
                    f'To teardown the TPU resources:\t{Style.BRIGHT}bash {self._managed_tpu[1]} {Style.RESET_ALL}\n'
                )

    def teardown(self, handle: ResourceHandle) -> None:
        _run(f'ray down -y {handle}', shell=True, check=True)
        if self._managed_tpu is not None:
            _run(f'bash {self._managed_tpu[1]}')
