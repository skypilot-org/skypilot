# SkyPilot On-prem

## Multi-tenancy
- Every user has their own job queue.
- Every user will start their own skylet (whenever `sky launch` is first called).

## Heterogeneous accelerator support
- Supports different types of accelerators across nodes (internode).
- Does not support different types of accelerators within the same node (intranode).

## Installing Ray and SkyPilot 
- Admin installs Ray==2.4.0 and SkyPilot globally on all machines. It is assumed that the admin regularly keeps SkyPilot updated on the cluster.
- Python >= 3.7 for all users.
- When a regular user runs `sky launch`, a local version of SkyPilot will be installed on the machine for each user. The local installation of Ray is specified in `sky/templates/local-ray.yml.j2`.

## Registering clusters as a regular user
- Registering clusters can be done in two steps:
  - Creating a cluster config in `~/.sky/local/`. This cluster is uninitialized, as SkyPilot has not registered the cluster into its database.
  - Running `sky launch -c [LOCAL_CLUSTER_NAME] ''` for the first time. This will intialize the cluster and register it into SkyPilot's database.
- `sky status` shows both initialized and uninitialized local clusters.

## Job submission pipeline
- Any `sky launch/exec` job is submitted via the Ray job submission interface.
- As the Ray cluster is launched by the admin user, any Ray remote functions will be run under the admin user by default. To see this, run the following snippet as a normal user:

```
def whoami():
  import subprocess
  subprocess.call(['whoami'])

# Should print current user
whoami()  

# Should print root user that started the Ray cluster
ray.get(ray.remote(f).remote())
```
  
- Therefore, SkyPilot On-prem transparently includes user-switching so that SkyPilot tasks are still run as the calling, unprivileged user. This user-switching (`sudo -H su --login [USER]` in appropriate places) works as follows:
    - In `sky/backends/cloud_vm_ray_backend.py::_setup_and_create_job_cmd_on_local_head`, switching between users is called during Ray job submission. The command `ray job submit --address=http://127.0.0.1:8266 --submission-id {ray_job_id} -- sudo -H su --login [SSH_USER] -c \"[JOB_COMMAND]\"` switches job submission execution from admin back to the original user `SSH_USER`. The `JOB_COMMAND` argument runs a bash script with the user's run commands.
    - In `sky/skylet/log_lib.py::run_bash_command_with_log`, there is also another `sudo -H su` command to switch users. The function `run_bash_command_with_log` is part of the `RayCodeGen` job execution script uploaded to remote for job submission (located in `~/.sky/sky_app/sky_app_[JOB_ID].py`). This program initially runs under the calling user, but it executes the function `run_bash_command_with_log` from the context of the admin, as the function is executed within the Ray cluster as a Ray remote function (see above for why all Ray remote functions are run under admin).
- SkyPilot ensures Ray-related environment variables (that are critical for execution) are preserved across switching users (check with `examples/env_check.yaml`).

## Miscellaneous
- `sky start/stop/autostop` is not supported.
- `sky down` is supported. The command `sky down` does not terminate the cluster, but it "disconnects" this user by killing the user's jobs in the cluster and removing the local cluster from `sky status`. Other users' jobs are not affected.
