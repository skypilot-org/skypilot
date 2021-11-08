"""
Runs a simple hello world ray program on a kubernetes cluster provisioned by sky.
"""
from sky.k8s.wrappers.sky_manager import SkyManager
from sky.k8s.wrappers.sky_task import SkyRayTask

if __name__ == '__main__':
    sky = SkyManager(cloud='aws')

    task = SkyRayTask(run_command=["python", "/cray_workloads/cray_workloads/drivers/cray_runscript.py"],
                      run_args=["--cray-utilfreq", "10", "--cray-logdir", "/cilantrologs", "--cray-workload-type",
                                "sleep_task", "--sleep-time", "0.2"],
                      docker_image="public.ecr.aws/cilantro/cray-workloads:latest",
                      setup_commands=None,
                      files_to_copy=None)
    # We can specify resources here, but for now assuming one task model

    resources = {'m5.2xlarge': 1,
                 'm5.xlarge': 1}

    # TODO(romilb): We can hide the kubeconfig arg complexity inside the SkyManager or rename it to something more user friendly
    # TODO(romilb): Replace with get_or_create_cluster() to make this call idempotent
    retcode, _, cluster_config = sky.create_cluster(cluster_name='MyCluster',
                                                    node_types=resources,
                                                    kubeconfig_path='myclusterconfig.yaml')

    sky.deploy_task(cluster_config, task)

    # How do we wait for task to complete?