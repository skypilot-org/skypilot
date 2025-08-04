# Run SkyPilot on AWS SageMaker HyperPod with EKS

This example shows how to run SkyPilot on AWS SageMaker HyperPod with EKS.

## Prerequisites

- An existing SageMaker HyperPod with EKS (or you can create one with AWS [doc](https://catalog.workshops.aws/sagemaker-hyperpod-eks/en-US/00-setup/own-account/01-workshop-infra-script))
- SkyPilot installed: [installation doc](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html)
```bash
pip install skypilot-nightly[kubernetes]
```


## Connect to HyperPod cluster

1. Get your EKS cluster name and region from the SageMaker HyperPod console.

```bash
aws eks update-kubeconfig --name $EKS_CLUSTER_NAME --region $EKS_REGION
```

2. Connect SkyPilot to the cluster.

```bash
sky check k8s
```

![](https://i.imgur.com/aZaocOt.png)

If you are using it with SkyPilot for the first time, you may see a hint to create GPU labels for your nodes. Follow the instructions to create the labels.

```bash
python -m sky.utils.kubernetes.gpu_labeler
```

## Find available GPUs

```bash
sky show-gpus --cloud k8s
```

![](https://i.imgur.com/pXPh5Li.png)



## Launch a SkyPilot cluster for interactive dev

```bash
sky launch -c dev --gpus A10G
```

![](https://i.imgur.com/5H0wid8.png)

This launches a SkyPilot cluster with a single A10G GPU, and you can use it as a interactive dev environment by sshing into it or [connecting to it with VSCode](https://docs.skypilot.co/en/latest/examples/interactive-development.html#vscode).

```bash
ssh dev
```

### Run a distributed training job

```bash
sky launch -c train train.yaml
```

<video src="https://i.imgur.com/bkABBNR.mp4" controls></video>

This will launch a distributed training job with 2 nodes on the HyperPod cluster. You can check the logs with:

```bash
sky logs train
```

To terminate the SkyPilot cluster, you can run:

```bash
sky down train
```



### Start many jobs


You can start many jobs to run them in parallel. See [this doc](https://docs.skypilot.co/en/latest/running-jobs/many-jobs.html) for more details.

```bash
for i in {1..10}; do
    sky jobs launch -n job-$i train.yaml --async
done
```

This will launch 10 distributed training jobs on the HyperPod cluster, with automatic recovery. You can check the status of all jobs with:

```bash
sky jobs queue
```

Or, find the jobs in a dashboard:

```bash
sky dashboard
```








