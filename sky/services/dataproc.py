import boto3
import json
import subprocess
import time

from google.cloud import dataproc_v1


def provision_cluster(cluster_name,
                      spark_version='3.2.1',
                      instance_type='n1-standard-8',
                      num_nodes=1,
                      region='us-central1'):

    project_id = 'intercloud-320520'
    # Create the cluster client.
    cluster_client = dataproc_v1.ClusterControllerClient(client_options={
        "api_endpoint": "{}-dataproc.googleapis.com:443".format(region)
    })

    # Create the cluster config.
    cluster = {
        "project_id": project_id,
        "cluster_name": cluster_name,
        "config": {
            "master_config": {
                "num_instances": 1,
                "machine_type_uri": instance_type
            },
            "software_config": {
                "properties": {
                    "dataproc:dataproc.allow.zero.workers": "true"
                }
            }
        },
    }
    if num_nodes > 1:
        cluster['config']['worker_config'] = {
            "num_instances": num_nodes - 1,
            "machine_type_uri": instance_type
        }
    print(f"Provisioning Dataproc cluster {cluster_name}...")
    # Create the cluster.
    operation = cluster_client.create_cluster(request={
        "project_id": project_id,
        "region": region,
        "cluster": cluster
    })
    result = operation.result()
    print(result)
    print("Cluster created successfully: {}".format(result.cluster_name))

    ips = []

    ip = subprocess.check_output([
        'gcloud', 'compute', 'instances', 'describe', f'{cluster_name}-m',
        '--format', 'get(networkInterfaces[0].networkIP)'
    ]).strip().decode("utf-8")
    ips.append(ip)

    if num_nodes > 1:
        for idx in range(0, num_nodes - 1):
            ip = subprocess.check_output([
                'gcloud', 'compute', 'instances', 'describe',
                f'{cluster_name}-w{idx}', '--format',
                'get(networkInterfaces[0].networkIP)'
            ]).strip().decode("utf-8")
            ips.append(ip)
    return ips


def terminate_cluster(cluster_name, region='us-central1'):
    project_id = 'intercloud-320520'
    cluster_client = dataproc_v1.ClusterControllerClient(client_options={
        "api_endpoint": "{}-dataproc.googleapis.com:443".format(region)
    })
    operation = cluster_client.delete_cluster(request={
        "project_id": project_id,
        "region": region,
        "cluster_name": cluster_name,
    })
