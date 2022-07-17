# Sky On-prem

## Multi-tenancy
- Every user has their own job queue.
- Every user will start their own skylet (whenever `sky launch` is first called).

## Heterogeneous accelerator support
- Supports different types of accelerators across nodes (internode).
- Does not support different types of accelerators within the same node (intranode).

## Installing Ray and Sky 
- Admin installs Ray==1.10.0 and Sky globally on all machines. It is assumed that the admin regularly keeps Sky updated on the cluster.
- Python >= 3.6 for all users.
- When a regular user runs `sky launch`, a local version of Sky will be installed on the machine for each user. The local installation of Ray is specified in `sky/templates/local-ray.yml.j2`.

## Registering clusters as a regular user
- Registering clusters can be done in two steps:
  - Creating a cluster config in `~/.sky/local/`. This cluster is uninitialized, as Sky has not registered the cluster into it's database.
  - Running `sky launch -c [LOCAL_CLUSTER_NAME] ''` for the first time. This will intialize the cluster and register it into Sky's database.
- `sky status` shows both initialized and uninitialized local clusters.

## Job submission pipeline
- Any `sky launch/exec` job is submitted via the Ray job submission interface.
- Since the cluster is launched by an admin, commands ran within Ray are run from the admin's perspective. To fix this, Sky On-prem runs `sudo -H su [USER]` to switch to the regular user to run the job. 
  - Switching between users happens two times during job submission:
    - In `sky/backends/cloud_vm_ray_backend.py::_setup_and_create_job_cmd_on_local_head`, switching between users is called during Ray job submission. The command `ray job submit --address=127.0.0.1:8265 --job-id {ray_job_id} -- sudo -H su --login [SSH_USER] -c \"[JOB_COMMAND]\"` switches job submission execution from admin back to the original user `SSH_USER`. The `JOB_COMMAND` argument runs a bash script with the user's run commands.
    - In `sky/skylet/log_lib.py::run_bash_command_with_log`, there is also another `sudo -H su` command to switch users. The function `run_bash_command_with_log` is part of the `RayCodeGen` job execution script uploaded to remote for job submission (located in `~/.sky/sky_app/sky_app_[JOB_ID].py`). This program initially runs under the calling user, but it executes the method `run_bash_command_with_log` from the context of the admin, as the function is executed within the Ray cluster.
      - To see why `run_bash_command_with_log` executes within the Ray cluster, a `futures` object is created by invoking `run_bash_command_with_log` with the corresponding placement group. When `ray.get(futures)` is called, the function executes under the admin account.
  - Sky ensures environment variables are preserved across switching users (check with `examples/env_check.yaml`).

## Miscellaneous
- `sky start/stop/autostop` is not supported.
- `sky down` is supported. The command `sky down` does not terminate the cluster, but it "disconnects" this user by killing the user's jobs in the cluster and removing the local cluster from `sky status`. Other users' jobs are not affected.
