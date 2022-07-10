# Sky On-prem

## Multi-tenancy
- Every user has their own job queue.
- Every user will start their own skylet (whenever `sky launch` is first called).

## Heterogeneous Accelerator Support
- Supports different types of accelerators across nodes (internode).
- Does not support different types of accelerators within the same node (intranode).

## Installing Ray and Sky 
- Admin installs Ray==1.10.0 and Sky globally on all machines. It is assumed that the admin regularly keeps Sky updated on the cluster.
- Python > 3.6 for all users.
- When a regular user runs `sky launch`, a local version of Sky will be installed on the machine for each user.

## Job Submission Pipeline
- Any `sky launch/exec` job is submitted via the Ray job submission interface.
- Since the cluster is launched by an admin, commands ran within Ray are run from the admin's perspective. To fix this, Sky On-prem runs `sudo -H su [USER]` to switch to the regular user to run the job. 
  - Switching between users happens two times during job submission:
    - During `ray job submit -- [COMMAND]` , `COMMAND` is run from the admin's perspective.
    - Sky wraps job execution with a python wrapper uploaded to head node in `~/.sky/sky_app/sky_app_[JOB_ID].py`. This program initially runs under the calling user, but it switches to admin when `ray.get()` is called.
  - Sky ensures environment variables are preserved across switching users (check with `examples/env_check.yaml`).

## Miscellaneous
- `sky start/stop/autostop` is not supported.
- `sky down` is supported. The command `sky down` does not terminate the cluster, but it "disconnects" this user by killing the user's jobs in the cluster and removing the local cluster from `sky status`. Other users' jobs are not affected.
