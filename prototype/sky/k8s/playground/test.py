#!/usr/bin/env python

##############################################################################
#
# Create a Kubernetes cluster on AWS using EKS.  You should know that this
# takes an absolute age, allow 20-30 mins.  That's just the speed stuff
# deploys at.
#
# This assumes that kubectl and heptio-authenticator-aws are in your PATH.
#
# This script works incrementally.  It creates things in sequence, and assumes
# that if they already exist, they're good.  If you get things breaking
# during deployment, you'll want to make sure you don't have something
# broken.
#
##############################################################################

import boto3
import boto3.ec2
import sys
import json
import time
import yaml
import subprocess
import os

############################################################################
# Config you might want to change
############################################################################

# AWS Region
region = "us-west-2"

# Cluster name
cluster_name = "analytics"

# K8s version to deploy
k8s_version = "1.10"

# Size of cluster.
node_group_min = 3
node_group_max = 5

# Worker instance types to deploy
instance_type = "t2.medium"

############################################################################
# Config you might not want to change
############################################################################

# Names for VPC and worker stack
vpc_name = cluster_name + "-vpc"
workers_name = cluster_name + "-workers"

# CloudFormation templates for VPC and worker nodes
vpc_template = "https://amazon-eks.s3-us-west-2.amazonaws.com/1.10.3/2018-06-05/amazon-eks-vpc-sample.yaml"
workers_template = "https://amazon-eks.s3-us-west-2.amazonaws.com/1.10.3/2018-06-05/amazon-eks-nodegroup.yaml"

# K8s IAM role name to use
k8s_admin_role_name = "EksServiceManagement"

# Config file to write kubectl configuration to.
config_file = "kubeconfig.yaml"

# Keypair for accessing workers.  Keypair name and filename.
keypair_name = "workers-key"
secret_file = "secret.pem"

# Image names for AMIs providing workers.
node_images = {
    "us-west-2": "ami-73a6e20b",
    "us-east-1": "ami-dea4d5a1"
}

# Filename to write internal auth thing to.
worker_auth = "aws-auth-cm.yaml"

############################################################################
# Connect AWS
############################################################################

# Connect to AWS, EC2, cloudformation, IAM and EKS
s = boto3.Session(region_name=region)
ec2 = s.client("ec2")
cf = s.client("cloudformation")
iam = s.client("iam")
eks = s.client("eks")

############################################################################
# IAM role for K8s
############################################################################

print
"*** IAM role"

try:

    # See if role exists.
    role = iam.get_role(RoleName=k8s_admin_role_name)
    print
    "IAM role exists."

except:

    print
    "IAM role does not exist.  Creating..."

    # This is an AWS role policy document.  Allows access for EKS.
    policy_doc = json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Action": "sts:AssumeRole",
                "Effect": "Allow",
                "Principal": {
                    "Service": "eks.amazonaws.com"
                }
            }
        ]
    })

    # Create role.
    iam.create_role(
        RoleName=k8s_admin_role_name,
        AssumeRolePolicyDocument=policy_doc,
        Description="Role providing access to EKS resources from EKS"
    )

    print
    "Role created."
    print
    "Attaching policies..."

    # Add policies allowing access to EKS API.

    iam.attach_role_policy(
        RoleName=k8s_admin_role_name,
        PolicyArn="arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
    )

    iam.attach_role_policy(
        RoleName=k8s_admin_role_name,
        PolicyArn="arn:aws:iam::aws:policy/AmazonEKSServicePolicy"
    )

    print
    "Attached."

# Get role ARN for later.
role = iam.get_role(RoleName=k8s_admin_role_name)
role_arn = role["Role"]["Arn"]


############################################################################
# VPC stack
############################################################################

# The VPC stack is a VPC and subnetworks to allow K8s communication.

# Check if a stack exists.
def stack_exists(cf, name):
    try:
        stack = cf.describe_stacks(StackName=name)
        return True
    except:
        return False


print
"*** VPC stack"
if stack_exists(cf, vpc_name):

    # If stack exists, do nothing.
    print
    "VPC stack already exists."

else:

    print
    "Creating VPC stack..."

    # Create VPC stack.
    response = cf.create_stack(
        StackName=vpc_name,
        TemplateURL=vpc_template,
        Parameters=[],
        TimeoutInMinutes=15,
        OnFailure='DELETE'
    )

    if response == None:
        print
        "Could not create VPC stack"
        sys.exit(1)

    if not "StackId" in response:
        print
        "Could not create VPC stack"
        sys.exit(1)

    # Get stack ID for later.
    stack_id = response["StackId"]

    print
    "Created stack " + vpc_name
    print
    "Waiting for VPC stack creation to complete..."

    try:

        # A waiter is something which polls AWS to find out if an operation
        # has completed.
        waiter = cf.get_waiter('stack_create_complete')

        # Wait for stack creation to complet
        res = waiter.wait(
            StackName=vpc_name,
        )

    except:

        # If waiter fails, that'll be the thing taking too long to deploy.
        print
        "Gave up waiting for stack to create"
        sys.exit(1)

    print
    "Stack created"

# Get output information from the stack: VPC ID, security group and subnet IDs.
stack = cf.describe_stacks(StackName=vpc_name)
vpc_sg = None
vpc_subnet_ids = None
vpc_id = None

# Loop over outputs grabbing information.
for v in stack["Stacks"][0]["Outputs"]:
    if v["OutputKey"] == "SecurityGroups": vpc_sg = v["OutputValue"]
    if v["OutputKey"] == "VpcId": vpc_id = v["OutputValue"]
    if v["OutputKey"] == "SubnetIds": vpc_subnet_ids = v["OutputValue"]

print
"VPC ID: %s" % vpc_id
print
"VPC security group: %s" % vpc_sg
print
"VPC subnet IDs: %s" % vpc_subnet_ids

# Split subnet IDs - it's comma separated.
vpc_subnet_ids = vpc_subnet_ids.split(",")

############################################################################
# EKS cluster
############################################################################

print
"*** EKS cluster"

try:
    cluster = eks.describe_cluster(name=cluster_name)
    print
    "Cluster already exists."

except:

    print
    "Creating cluster (ETA ~10 minutes)..."

    # Create Kubernetes cluster.
    response = eks.create_cluster(
        name=cluster_name,
        version=k8s_version,
        roleArn=role_arn,
        resourcesVpcConfig={
            "subnetIds": vpc_subnet_ids,
            "securityGroupIds": [vpc_sg]
        }
    )

    print
    "Cluster creation initiated."
    print
    "Waiting for completion (ETA 10 minutes)..."

    # Wait for two minutes before doing the checking.
    time.sleep(120)

    # Going to give up after 40 times 20 seconds.  Btw, EKS API / boto3 doesn't
    # currently support a 'waiter' object to wait for the cluster to start,
    # which would be nice.
    i = 40
    while True:

        # Wait 20 seconds
        time.sleep(20)

        try:

            # Get cluster status
            cluster = eks.describe_cluster(name=cluster_name)
            status = cluster["cluster"]["status"]

            print
            "Cluster status: %s" % status

            # Is it active? If so break out of loop
            if status == "ACTIVE":
                break

            # Maybe give up after so many goes.
            i = i - 1
            if i <= 0:
                print
                "Given up waiting for cluster to go ACTIVE."
                sys.exit(1)

        except Exception, e:
            print
            "Exception:", e

    print
    "Cluster active."

# Get cluster stuff
cluster = eks.describe_cluster(name=cluster_name)

# This spots the case where the cluster isn't in an expected state.
status = cluster["cluster"]["status"]
if status != "ACTIVE":
    print
    "Cluster status %s, should be ACTIVE!" % status
    sys.exit(1)

# Get cluster endpoint and security info.
cluster_cert = cluster["cluster"]["certificateAuthority"]["data"]
cluster_ep = cluster["cluster"]["endpoint"]

print
"Cluster: %s" % cluster_ep

############################################################################
# K8s config
############################################################################

print
"*** EKS configuration"

# This section creates a Kubernetes kubectl configuration file if one does
# not exist.

if os.path.isfile(config_file):
    print
    "Config exists already."
else:

    cluster_config = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "cluster": {
                    "server": str(cluster_ep),
                    "certificate-authority-data": str(cluster_cert)
                },
                "name": "kubernetes"
            }
        ],
        "contexts": [
            {
                "context": {
                    "cluster": "kubernetes",
                    "user": "aws"
                },
                "name": "aws"
            }
        ],
        "current-context": "aws",
        "preferences": {},
        "users": [
            {
                "name": "aws",
                "user": {
                    "exec": {
                        "apiVersion": "client.authentication.k8s.io/v1alpha1",
                        "command": "heptio-authenticator-aws",
                        "args": [
                            "token", "-i", cluster_name
                        ]
                    }
                }
            }
        ]
    }

    # Write in YAML.
    config_text = yaml.dump(cluster_config, default_flow_style=False)
    open(config_file, "w").write(config_text)

    print
    "Written to %s." % config_file

    # Having written the kubectl configuration, should check it works...
    print
    "Try kubectl command..."

    resp = subprocess.call(["kubectl", "--kubeconfig=%s" % config_file, "get",
                            "nodes"])

    if resp != 0:
        print
        "The kubectl command didn't work."
        sys.exit(1)

    print
    "All working."

############################################################################
# Keypair for workers
############################################################################

print
"*** Keypairs"

try:

    # Check if keypair exists, if not, ignore this step.
    key = ec2.describe_key_pairs(
        KeyNames=[keypair_name]
    )

    print
    "Keypair exists."

except:

    # Create keypair
    print
    "Creating keypair..."
    resp = ec2.create_key_pair(KeyName=keypair_name)
    private_key = resp["KeyMaterial"]

    # Delete old keyfile.
    print
    "Writing key file..."
    try:
        os.unlink(secret_file)
    except:
        pass

    # Write new keypair to file.
    open(secret_file, "w").write(private_key)
    os.chmod(secret_file, 0400)

    print
    "Written to %s." % secret_file

############################################################################
# Workers
############################################################################

# In this step a stack of worker instances is created using CloudFormation.

# Check if stack exists.
print
"*** Workers stack"
if stack_exists(cf, workers_name):
    print
    "Workers stack already exists."
else:

    print
    "Creating workers stack..."

    # Create stack
    response = cf.create_stack(
        StackName=workers_name,
        TemplateURL=workers_template,
        Capabilities=["CAPABILITY_IAM"],
        Parameters=[
            {
                "ParameterKey": "ClusterName",
                "ParameterValue": cluster_name
            },
            {
                "ParameterKey": "ClusterControlPlaneSecurityGroup",
                "ParameterValue": vpc_sg
            },
            {
                "ParameterKey": "NodeGroupName",
                "ParameterValue": cluster_name + "-worker-group"
            },
            {
                "ParameterKey": "NodeAutoScalingGroupMinSize",
                "ParameterValue": str(node_group_min)
            },
            {
                "ParameterKey": "NodeAutoScalingGroupMaxSize",
                "ParameterValue": str(node_group_max)
            },
            {
                "ParameterKey": "NodeInstanceType",
                "ParameterValue": instance_type
            },
            {
                "ParameterKey": "NodeImageId",
                "ParameterValue": node_images[region]
            },
            {
                "ParameterKey": "KeyName",
                "ParameterValue": keypair_name
            },
            {
                "ParameterKey": "VpcId",
                "ParameterValue": vpc_id
            },
            {
                "ParameterKey": "Subnets",
                "ParameterValue": ",".join(vpc_subnet_ids)
            }
        ],
        TimeoutInMinutes=15,
        OnFailure='DELETE'
    )

    if response == None:
        print
        "Could not create worker group stack"
        sys.exit(1)

    if not "StackId" in response:
        print
        "Could not create worker group stack"
        sys.exit(1)

    print
    "Initiated workers (ETA 5-20 mins)..."

    print
    "Waiting for workers stack creation to complete..."

    try:
        # This is a water which waits for the stack deployment to complete.
        waiter = cf.get_waiter('stack_create_complete')
        res = waiter.wait(
            StackName=workers_name
        )
    except:
        print
        "Gave up waiting for stack to create"
        sys.exit(1)

    print
    "Stack created"

# Get stack info.
stack = cf.describe_stacks(StackName=workers_name)
node_instance_role = None

# We need NodeInstanceRole output.
for v in stack["Stacks"][0]["Outputs"]:
    if v["OutputKey"] == "NodeInstanceRole": node_instance_role = v["OutputValue"]

print
"Node instance role: %s" % node_instance_role

############################################################################
# Introduce workers to master
############################################################################

# Final step is to deploy a config map which tells the master how to
# contact the workers.

print
"*** Update worker auth"

# This is horrific.  Can't get K8s to use a file created by PyYAML.
config = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: aws-auth\n" + \
         "  namespace: kube-system\ndata:\n  mapRoles: |\n" + \
         "    - rolearn: " + node_instance_role + "\n" + \
         "      username: system:node:{{EC2PrivateDNSName}}\n" + \
         "      groups:\n        - system:bootstrappers\n        - system:nodes\n"

print
"Write config map..."

open(worker_auth, "w").write(config)

resp = subprocess.call(["kubectl", "--kubeconfig=%s" % config_file, "apply",
                        "-f", worker_auth])

if resp != 0:
    print
    "The kubectl command didn't work."
    sys.exit(1)

print
"Written."

print
"Try:"
print
"  kubectl --kubeconfig=%s get nodes" % config_file
