import boto3
import json
import time


def provision_cluster(cluster_name,
                      spark_version='3.2.1',
                      instance_type='m5.4xlarge',
                      num_nodes=1,
                      region='us-east-2',
                      skip_provision=False):

    service_dict = {
        '3.2.1': 'emr-6.7.0',
        '3.2.0': 'emr-6.6.0',
        '3.1.2': 'emr-6.5.0'
    }
    client = boto3.client('emr', region_name=region)

    if not skip_provision:
        instance_groups = [
            {
                'Name': 'Master',
                'Market': 'ON_DEMAND',
                'InstanceRole': 'MASTER',
                'InstanceType': instance_type,
                'InstanceCount': 1,
                'EbsConfiguration': {
                    'EbsBlockDeviceConfigs': [{
                        'VolumeSpecification': {
                            'VolumeType': 'gp2',
                            'SizeInGB': 256
                        },
                        'VolumesPerInstance': 1
                    },],
                }
            },
        ]
        if num_nodes > 1:
            instance_groups.append(
                {
                    'Name': 'Core',
                    'Market': 'ON_DEMAND',
                    'InstanceRole': 'CORE',
                    'InstanceType': instance_type,
                    'InstanceCount': num_nodes - 1,
                    'EbsConfiguration': {
                        'EbsBlockDeviceConfigs': [{
                            'VolumeSpecification': {
                                'VolumeType': 'gp2',
                                'SizeInGB': 256
                            },
                            'VolumesPerInstance': 1
                        },],
                    }
                },)
        response = client.run_job_flow(
            Name=cluster_name,
            ReleaseLabel=service_dict[spark_version],
            Instances={
                'TerminationProtected': False,
                'KeepJobFlowAliveWhenNoSteps': True,
                'InstanceGroups': instance_groups,
                'Ec2KeyName': 'sky-key-a13938'
            },
            Applications=[{
                'Name': 'Spark'
            }, {
                'Name': 'Hadoop'
            }, {
                'Name': 'Hive'
            }, {
                'Name': 'Pig'
            }, {
                'Name': 'Hue'
            }],
            VisibleToAllUsers=True,
            ServiceRole='EMR_DefaultRole',
            JobFlowRole='EMR_EC2_DefaultRole',
            AutoScalingRole="EMR_AutoScaling_DefaultRole")
    cluster_ready = False
    while not cluster_ready:
        wait_response = client.list_clusters(ClusterStates=['WAITING'])
        ready_clusters = wait_response['Clusters']
        for clus in ready_clusters:
            if clus['Name'] == cluster_name:
                cluster_id = clus['Id']
                cluster_ready = True
        print(f'Waiting for EMR Cluster {cluster_name} to finish provisioning.')
        time.sleep(5)

    instance_reponse = client.list_instances(ClusterId=cluster_id)['Instances']
    ips = []
    for instance in instance_reponse:
        ips.append(instance['PublicIpAddress'])
    return ips


def terminate_cluster(cluster_name, region='us-east-2'):
    client = boto3.client('emr', region_name=region)
    response = client.list_clusters(ClusterStates=['WAITING'])
    ready_clusters = response['Clusters']
    cluster_id = None
    for idx, clus in enumerate(ready_clusters):
        if clus['Name'] == cluster_name:
            cluster_id = clus['Id']
    try:
        response = client.terminate_job_flows(JobFlowIds=[cluster_id])
    except:
        pass
    return response
