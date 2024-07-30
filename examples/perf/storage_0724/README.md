# Benchmarking Storage options for K8s clusters

This directory is a collection of YAMLs used to benchmark SkyPilot's performance.

## Kubernetes cluster setup

We use GKE.

Use pd-ssd for boot disk for best performing local disk. Remember to add additional local SSDs required for ceph and 
other storage options (select Raw Block Local SSDs).

Use Ubuntu image because COS does not have RBD, required by [Ceph](https://rook.io/docs/rook/latest/Getting-Started/Prerequisites/prerequisites/#kernel) and [Longhorn](https://longhorn.io/docs/archives/1.3.0/advanced-resources/os-distro-specific/csi-on-gke/).


Command to create a GKE cluster with 3 n2-standard-8 nodes and 2 a2-highgpu-8g nodes with 8 Nvidia A100 GPUs each:

```
gcloud beta container --project "skypilot-375900" clusters create "gkegpu" --region "asia-southeast1" --no-enable-basic-auth --cluster-version "1.29.5-gke.1091002" --release-channel "stable" --machine-type "n2-standard-8" --image-type "UBUNTU_CONTAINERD" --disk-type "pd-ssd" --disk-size "200" --metadata disable-legacy-endpoints=true --scopes "https://www.googleapis.com/auth/devstorage.read_only","https://www.googleapis.com/auth/logging.write","https://www.googleapis.com/auth/monitoring","https://www.googleapis.com/auth/servicecontrol","https://www.googleapis.com/auth/service.management.readonly","https://www.googleapis.com/auth/trace.append" --num-nodes "3" --logging=SYSTEM,WORKLOAD --enable-ip-alias --network "projects/skypilot-375900/global/networks/default" --subnetwork "projects/skypilot-375900/regions/asia-southeast1/subnetworks/default" --no-enable-intra-node-visibility --default-max-pods-per-node "110" --security-posture=standard --workload-vulnerability-scanning=disabled --no-enable-master-authorized-networks --addons HorizontalPodAutoscaling,HttpLoadBalancing,GcePersistentDiskCsiDriver --enable-autoupgrade --enable-autorepair --max-surge-upgrade 1 --max-unavailable-upgrade 0 --binauthz-evaluation-mode=DISABLED --enable-managed-prometheus --enable-shielded-nodes --node-locations "asia-southeast1-b" && gcloud beta container --project "skypilot-375900" node-pools create "gpunodes" --cluster "gkegpu" --region "asia-southeast1" --machine-type "a2-highgpu-8g" --accelerator "type=nvidia-tesla-a100,count=8" --image-type "UBUNTU_CONTAINERD" --disk-type "pd-ssd" --disk-size "200" --metadata disable-legacy-endpoints=true --scopes "https://www.googleapis.com/auth/devstorage.read_only","https://www.googleapis.com/auth/logging.write","https://www.googleapis.com/auth/monitoring","https://www.googleapis.com/auth/servicecontrol","https://www.googleapis.com/auth/service.management.readonly","https://www.googleapis.com/auth/trace.append" --num-nodes "2" --enable-autoupgrade --enable-autorepair --max-surge-upgrade 1 --max-unavailable-upgrade 0 --node-locations "asia-southeast1-b"
```

Install nvidia drivers:
```
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/ubuntu/daemonset-preloaded.yaml
```

Create GCS bucket for testing SkyStore in the same reigon as the GKE cluster.
```
gsutil mb -l asia-southeast1 gs://sky-data-benchmark
```



## Results

### GCSFuse
```
(storage-demo, pid=1722) ===== Benchmark Results =====
(storage-demo, pid=1722) All results are reported as (bandwidth, IOPS)
(storage-demo, pid=1722) 
(storage-demo, pid=1722) ##### Sequential Read Results #####
(storage-demo, pid=1722)     799.06 MB/s     762.05 IOPS
(storage-demo, pid=1722) 
(storage-demo, pid=1722) ##### Sequential Write Results #####
(storage-demo, pid=1722)     550.07 MB/s     524.59 IOPS
(storage-demo, pid=1722) 
(storage-demo, pid=1722) ##### Small Files Read Results #####
(storage-demo, pid=1722)     160.33 MB/s     152.91 IOPS
(storage-demo, pid=1722) 
(storage-demo, pid=1722) ##### Small Files Write Results #####
(storage-demo, pid=1722)     2.71 MB/s       2.59 IOPS
```

