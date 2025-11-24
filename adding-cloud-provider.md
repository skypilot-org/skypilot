# Adding a Cloud to SkyPilot v2

| ℹ️ NOTE: This guide will change as SkyPilot develops. Please check back often to make sure you have the most up-to-date version. Last updated: 12/16/2023 |
| :---- |

This is a guide to **help developers add new clouds to SkyPilot**. It starts with a high-level overview and then goes through the implementation step-by-step.

Please feel free to reach out to the SkyPilot team if you have any questions, issues, comments, or feedback (you can [join our Slack here](http://slack.skypilot.co)). We’re happy to help\!

Reference: PRs of recent cloud additions (these folks have used the doc to guide their development):

* [GCP](https://github.com/skypilot-org/skypilot/pull/2681)  
* [RunPod](https://github.com/skypilot-org/skypilot/pull/2829)

| ℹ️ NOTE: SkyPilot supports two types of provisioners: *ray* and *cloud-api* (recommended). The ray provisioner is based on the [Ray Autoscaler](https://docs.ray.io/en/latest/cluster/vms/references/ray-cluster-cli.html#launching-a-cluster-ray-up) (e.g., implementation of [IBM](https://github.com/skypilot-org/skypilot/tree/master/sky/skylet/providers/ibm), [OCI](https://github.com/skypilot-org/skypilot/tree/master/sky/skylet/providers/oci), [Lambda Cloud](https://github.com/skypilot-org/skypilot/tree/master/sky/skylet/providers/lambda_cloud), etc.). However, the ray provisioner is deprecated and so newly added clouds should implement the cloud-api. The *cloud-api* provisioner API is much simpler and more efficient. It is at least 2x faster to launch multi-node clusters with *cloud-api* than *ray* provisioner. This guide contains all instructions for implementing a new cloud in *cloud-api* provisioner. |
| :---- |

## Introduction to SkyPilot

[SkyPilot](https://github.com/skypilot-org/skypilot) is an intercloud broker \-- a framework for running workloads on any cloud. Here are some useful links to learn more:

1. [Introductory Blogpost](https://medium.com/@zongheng_yang/skypilot-ml-and-data-science-on-any-cloud-with-massive-cost-savings-244189cc7c0f) \[Start here if you are new\]  
2. [Documentation](https://skypilot.readthedocs.io/en/latest/)  
3. [The Sky Above the Clouds](https://arxiv.org/abs/2205.07147)  
4. [GitHub](https://github.com/skypilot-org/skypilot)

## How does SkyPilot work?

Here's a simplified overview of SkyPilot's architecture.

Figure 1: Architecture

In this diagram, the user has two clouds enabled (AWS and GCP). This is what happens when a user launches a job with sky launch:

1. The optimizer reads AWS and GCP Catalog from the cloud class and runs an algorithm to decide which cloud to run the job. (Let's suppose the optimizer chooses AWS.) This information is then sent to the provisioner+executor.  
* A catalog is a list of instance types and their prices.  
2. The provisioner+executor executes commands to launch a cluster on AWS.  
* AWS Provisioner is the interface between SkyPilot and AWS, translating launching operations to cloud APIs.  
3. Once the cluster is launched, the provisioner+executor ssh’s into the cluster to execute some AWS Setup commands. This is used to download some important packages on the cluster.  
4. The provisioner+executor submits the job to the cluster and the cluster runs the job.

When all is done, the user can run sky down and provisioner+executor will tear down the cluster by executing more ray commands.

## Adding a New Cloud \[Overview\]

SkyPilot currently supports three clouds with the new provisioner API (AWS, GCP, and RunPod). Now let's say you have a new cloud, called **FluffyCloud**, that you want SkyPilot to support. What do you need to do?

Six components to be implemented (the four circled number in Figure 1):

1. **Catalog:** [https://github.com/skypilot-org/skypilot-catalog/tree/master/catalogs/v6](https://github.com/skypilot-org/skypilot-catalog/tree/master/catalogs/v6)   
   The catalog file that contains the resources supported by fluffycloud  
2. **Catalog reader**: [sky/clouds/service\_catalog/](https://github.com/skypilot-org/skypilot/tree/master/sky/clouds/service_catalog)fluffycloud\_catalog.py  
   The functions to parse the catalog.  
3. **Cloud class**: [sky/clouds/](https://github.com/skypilot-org/skypilot/tree/master/sky/clouds)fluffycloud.py

The cloud class that handles the metadata of the clouds

4. **Cluster config**: [sky/templates/](https://github.com/skypilot-org/skypilot/tree/master/sky/templates)fluffycloud-ray.yaml.j2  
   The template cluster config file to be used by provisioner.  
5. **Provisioner**: [sky/provision/](https://github.com/skypilot-org/skypilot/tree/master/sky/provision)fluffycloud/instance.py  
   The provisioner of the cloud.  
6. **Miscs handling in backend**: [sky/backends/](https://github.com/skypilot-org/skypilot/tree/master/sky/backends)cloud\_vm\_ray\_backend.py  
   Add error handling and template registration for the cloud.

\=  
For reference, here is **an actual PR** for adding a new cloud to help you estimate what is required:

* RunPod: [https://github.com/skypilot-org/skypilot/pull/2829](https://github.com/skypilot-org/skypilot/pull/2829) 

### What Does FluffyCloud API Need to Support?

SkyPilot does not require all APIs to be supported by a cloud. That being said, there are a few basic things that FluffyCloud needs to support to be added to SkyPilot. FluffyCloud API **must be** able to:

* Launch an ssh-able instance in a region/zone.  
* Terminate an instance given instance id.  
* List the details (e.g. id, status, ip, name) of currently running instances.

## Adding a New Cloud \[Implementation\]

The rest of this document is a step-by-step implementation guide on how to add FluffyCloud to SkyPilot.

### Step 0: Get code

1. Clone the [SkyPilot repository](https://github.com/skypilot-org/skypilot.git) and follow the instructions to install SkyPilot from the [here](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html) to install SkyPilot from source.  
   `git clone https://github.com/skypilot-org/skypilot`  
2. Clone the [SkyPilot catalog repository](https://github.com/skypilot-org/skypilot-catalog).  
   `git clone https://github.com/skypilot-org/skypilot-catalog`  
3. Clone the fluffycloud repository.  
   `git clone https://github.com/michaelvll/skypilot-new-cloud`

### Step 1: Catalog

The catalog file should contain the available resources supported by the cloud with the hourly pricing. Please check out the schema at [https://github.com/skypilot-org/skypilot-catalog/README.md](https://github.com/skypilot-org/skypilot-catalog/README.md).

The catalog can be created manually, and submitted to the repository with the following step:  
**Create the catalog file for VMs at:** `skypilot-catalog/catalogs/v6/fluffycloud/vms.csv`

```
InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,Price,Region,GpuInfo,SpotPrice
```

For example, a catalog entry for Lambda Cloud would look like this:

```
InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,Price,Region,GpuInfo,SpotPrice
gpu_8x_v100,v100,8.0,92.0,448.0,4.4,us-east-1,"{'gpus': [{'name': 'v100', 'manufacturer': 'nvidia', 'count': 8.0, 'MemoryInfo': {'SizeInMiB': 16384}}], 'totalgpumemoryinmib': 16384}",
```

To see more examples of catalog entries, you should take a look [here](https://github.com/skypilot-org/skypilot-catalog/tree/master/catalogs/v5). In particular, look for `vms.csv` files (e.g. `aws/vms.csv`).

For testing purposes, let’s create a simple catalog with one entry. We can add to this catalog later: create a catalog at `~/.sky/catalogs/v5/fluffycloud/vms.csv` and add one catalog entry.

#### \[Optional\] Automatic Update for Catalogs

We use the GitHub Actions to update the catalogs hosted on the catalog repository. Please check out the examples at: `skypilot-catalog/.github/workflows`

In order to update the catalogs automatically, you may want to create a service catalog fetcher at: `skypilot/clouds/service_catalog/data_fetchers/fetch_aws.py`  
where we call the cloud APIs to fetch the latest catalogs.

| ℹ️ NOTE: For a new cloud, you can safely defer this optimization until later, and start with static catalog files like vms.csv. |
| :---- |

#### \[Optional\] Catalog for Images

If the VMs need specific images for different resources, an `images.csv` can be created at:  
`skypilot-catalog/catalogs/v5/fluffycloud/images.csv`

### Step 2: Catalog Reader

Now let's implement some functions that parse and read the catalog. This is very easy since most of this functionality has been implemented; we're simply calling the appropriate functions.

Copy [`skypilot-new-cloud`](https://github.com/Michaelvll/skypilot-new-cloud/blob/master/fluffycloud/fluffycloud_catalog.py)`/fluffycloud/fluffy_catalog.py` and place it at `skypilot/sky/clouds/service_catalog/fluffycloud_catalog.py`, and make any required changes to this file.

### Step 3: Cloud class

Now let's create the FluffyCloud class. This class calls some service catalog functions and contains code that checks FluffyCloud credentials. Many of the functions in this class are also straightforward to implement.

1. Create a copy of [`skypilot-new-cloud/fluffycloud/fluffycloud.py`](https://github.com/Michaelvll/skypilot-new-cloud/blob/master/fluffycloud/fluffycloud.py) and place it at `skypilot/sky/clouds/fluffycloud.py`.  
2. Implement the TODOs in the file.

### Step 4: Cluster Config {#step-4:-cluster-config}

Now let's write the FluffyCloud config file. This code is to store the configurations of the node to be created and the setup scripts that will be run once the node is provisioned to set up the runtime of SkyPilot and start the ray cluster for job scheduling.

1. Create a copy of [`skypilot-new-cloud`](http://skypilot-new-cloud/fluffycloud-ray.yml.j2)`/fluffycloud/fluffycloud-ray.yml.j2` and place it at `skypilot/sky/templates/fluffycloud-ray.yml.j2`  
2. You can place any configurations that will be used to create the instances under:

```
available_node_types:
  ray.head.default:
    node_config:
    # TODO: Add configurations of the instances to be created.
```

The templates is filled by the function `write_cluster_config` in `skypilot/backends/backend_utils.py`

For reference, [here](https://github.com/skypilot-org/skypilot/blob/master/sky/templates/aws-ray.yml.j2) is the AWS cluster config, [here](https://github.com/skypilot-org/skypilot/blob/master/sky/templates/aws-ray.yml.j2) is the GCP cluster config, and [here](https://github.com/skypilot-org/skypilot/blob/master/sky/templates/runpod-ray.yml.j2) is the RunPod cluster config.

### Step 5: Provisioner

Let's implement the provisioner APIs for FluffyCloud to interact with the cloud with cloud-specific APIs. We designed a minimal set of APIs that is required to make SkyPilot able to create, terminate, stop, and check cluster status.

1. Create the following provisioner structure:  
   `skypilot/sky/provision/`  
   `fluffycloud/`

   `__init__.py`

   `instance.py`  
2. Copy the content of the files `skypilot/sky/provision/gcp/__init__.py` and `skypilot/sky/provision/gcp/instance.py` to the newly created files for fluffycloud.

The implementation of GCP provisioner uses tags (labels) to store metadata and name of the cluster on the cloud to identify the instances created on the cloud. If your cloud does not support tags, you could use the instance name instead, e.g. RunPod implementation [here](https://github.com/skypilot-org/skypilot/blob/master/sky/provision/runpod/instance.py).

Now let’s implement a provisioner for FluffyCloud:

1. `bootstrap_instances` Bootstrap configurations for a cluster.   
   This is to automatically fill out extra configurations based on the configurations in the cluster config file implemented in [Step 4](#step-4:-cluster-config). For example:  
- Find the subnet to be used by the cluster ([GCP](https://github.com/skypilot-org/skypilot/blob/1cd3bb56ee9fd00a2b2489c82e72186abae22f17/sky/provision/gcp/config.py#L513C24-L513C24))  
- Create or get the IAM role (AWS, GCP)  
2. `run_instances` Start or resume instances with bootstrapped configuration.  
   This is used to start or resume instances with the cluster name. It should contain several steps:  
1. Get running nodes of the cluster on the cloud  
2. Check the remaining number of instances to satisfy `config.count`  
3. First try to resume any stopped instances  
4. Try to create the remaining instances from scratch to satisfy `config.count`  
3. `terminate_instances` Terminate running or stopped instances with `cluster_name`  
4. `wait_instances` Wait instances until they end up in the given state.  
5. `get_cluster_info` Get the metadata of instances in a cluster.  
6. `query_instances` Find the statuses of the nodes in a cluster.  
7. `stop_instances` (Optional) Stop running instances.  
8. `open_ports` (Optional) Open ports on a launched cluster  
9. `cleanup_ports` (Optional) Cleanup ports when terminating the cluster.

| ℹ️ NOTE: The code in provision/fluffycloud assumes that cloud credentials are already stored locally on your computer. |
| :---- |

### Step 6: FluffyCloud Miscs Handling in Backend

Now let's make some miscs handling in backend for the FluffyCloud.

1. Change `_get_cluster_config_template` in sky/backends/cloud\_vm\_ray\_backend.py according to the TODO statement found in [skypilot-new-cloud/fluffycloud/fluffycloud-ray-more.py](https://github.com/Michaelvll/skypilot-new-cloud/blob/master/fluffycloud/fluffycloud-ray-more.py).

Finally, there are some more miscellaneous places that we need to make changes to.

2. Implement the TODO statements found in [skypilot-new-cloud/fluffycloud/misc.py](https://github.com/Michaelvll/skypilot-new-cloud/blob/master/fluffycloud/misc.py)  
3. Copy [skypilot-new-cloud/fluffycloud/adaptors.py](https://github.com/Michaelvll/skypilot-new-cloud/blob/master/fluffycloud/adaptors.py) to `sky/adaptors/fluffycloud.py`

### Step 7: Testing

1. Check credentials: `sky check`  
2. `sky launch -c test-single-instance --cloud fluffycloud echo hi`  
3. `sky stop test-single-instance; sky start test-single-instance`  
4. `sky launch -c test-single-instance echo hi`  
5. `sky down test-single-instance`  
6. Terminate a stopped instance: `sky launch -c test-single-instance --cloud fluffycloud echo hi; sky stop test-single-instance; sky down test-single-instance`  
7. `sky launch --cloud fluffycloud -c test-autostop -i 1 echo hi`  
8. `sky launch --cloud fluffycloud -c test-autodown -i 1 --down echo hi`  
9. Make sure it works with other clouds: `sky launch -c test-all echo hi`  
10. Mark unrelated tests in `tests/test_smoke.py` and run `pytest tests/test_smoke.py --fluffycloud`

**💡 Note**: **Always run `sky api stop; sky api start`** for any code changes to take effect on the local API server. 

| ⚠️ TODO: Talk about testing and add a reminder to expand the catalog after basic tests pass. |
| :---- |

## 

## Workarounds

## FAQ

### My cloud uses a VPC and autodown cannot delete it. What should I do?

This usually occurs when:

1. You create one VPC for each cluster.  
2. The VPC cannot be deleted until all the nodes in the cluster have been terminated.

The reason this occurs is that autodown is implemented by running ray down on the head node.

Here are some possible solutions:

1. Use a default VPC for all clusters in a region and don’t delete these VPCs (works if not expensive).  
2. Find a way to delete VPC and node at the same time (e.g. deleting an entire resource group at once).  
3. Disable the autodown feature for your cloud until a better solution can be found.

| ⚠️ TODO:Add more FAQ. |
| :---- |

