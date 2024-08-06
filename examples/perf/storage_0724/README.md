# Benchmarking Storage options

This directory is a collection of YAMLs used to benchmark performance of different storage options on SkyPilot.

### Setup

Provision a A100:1 cluster with `sky launch -c a100 --gpus A100:1 --disk-tier best --region me-west1`

#### GCSFuse/SkyStorage
Use `sky status -a` to get the region and zone of the cluster. Use the region to create a GCS bucket in the same region.

```
gsutil mb -l me-west1 gs://<bucketname>
```

Run `sky launch -c a100 setup_gcsfuse.yaml` to get the bucket mounted.

For GCSFuse/RClone VFS Nemo dataset reading, create a bucket in the same region and copy data over to it
```bash
gsutil mb -l me-west1 gs://sky-wiki-data-me
gsutil rsync -r gs://sky-wiki-data/ gs://sky-wiki-data-me
```

#### RClone VFS

Run `sky exec -c a100 setup_rclone.yaml` to setup RClone VFS on the YAML.

#### JuiceFS

Guide for setting up on Google Cloud infra - https://colab.research.google.com/drive/1wA8vRwqiihXkI6ViDU8Ud868UeYtmCo5

Will need to deploy a Postgres CloudSQL instance in the same region using cloud console.
* Make sure to create a database named `juicefs` once the postgres instance is running.  

Also setup the bucket with 
```
gsutil mb -l me-west1 gs://sky-juicefs-bucket
```

Install cloud_sql_proxy on the VM
```bash
curl -o cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.12.0/cloud-sql-proxy.linux.amd64
chmod +x cloud-sql-proxy
````


Connect to the CloudSQL instance using the cloud_sql_proxy
```
./cloud-sql-proxy skypilot-375900:me-west1:juicefs-exp-meta --address 0.0.0.0 --port 5432
```

Install juicefs
```
curl -sSL https://d.juicefs.com/install | sh -
```

Create a volume. Use postgres password here. Can also be set with META_PASSWORD env variable.
```
juicefs format \
    --storage gs \
    --bucket gs://sky-juicefs-bucket \
    "postgres://postgres:<password>@localhost:5432/juicefs?sslmode=disable" \
    myjfs
``` 

You'll see something like
```
juicefs format     --storage gs     --bucket gs://sky-juicefs-bucket     "postgres://postgres:qweasdzxc@localhost:5432/juicefs?sslmode=disable"     myjfs
2024/07/31 22:04:09.192792 juicefs[17412] <INFO>: Meta address: postgres://postgres:****@localhost:5432/juicefs?sslmode=disable [interface.go:504]
2024/07/31 22:04:09.218047 juicefs[17412] <WARNING>: The latency to database is too high: 25.034684ms [sql.go:311]
2024/07/31 22:04:09.230578 juicefs[17412] <INFO>: Data use gs://sky-juicefs-bucket/myjfs/ [format.go:484]
2024/07/31 22:04:10.616649 juicefs[17412] <INFO>: Volume is formatted as {
  "Name": "myjfs",
  "UUID": "cf1ba0c3-e364-4a2a-8170-ce1c351109a8",
  "Storage": "gs",
  "Bucket": "gs://sky-juicefs-bucket",
  "BlockSize": 4096,
  "Compression": "none",
  "EncryptAlgo": "aes256gcm-rsa",
  "TrashDays": 1,
  "MetaVersion": 1,
  "MinClientVersion": "1.1.0-A",
  "DirStats": true,
  "EnableACL": false
} [format.go:521]
```

Now you can mount. Use the -d flag to run as a daemon.
```
sudo mkdir -p /testmnt
sudo chown $USER:$USER /testmnt
juicefs mount postgres://postgres:<password>@localhost:5432/juicefs?sslmode=disable /testmnt
```
Once you have done the setup once, you can `sky exec setup_juicefs.yaml` to mount it on VMs.

#### Google Filestore 

Filestore is a managed NFS service. Create a filestore instance in the same region as the cluster using the cloud console. 
MAKE SURE TO USE NFS 4.1 IN FILESTORE SETUP IN THE CLOUD CONSOLE. Nvidia Nemo image fails with protocol not supported error with NFS 3.

```bash
export MOUNT_DIR=/nfs
sudo apt-get -y update &&
sudo apt-get install nfs-common
sudo mkdir -p $MOUNT_DIR
sudo chown $USER:$USER $MOUNT_DIR
```

Get the IP from the googlefilestore CLI
```
sudo mount -o rw,intr 10.125.158.130:/skynfs $MOUNT_DIR
```

### Running the benchmarks

#### Synthetic benchmarks
Run `sky exec -c a100 --env BENCH_PATH=/skystorage-mount synthetic_benchmarks.yaml` to run the synthetic benchmarks.

#### Real world - NeMo Read + Write (Checkpoint)
See yamls in nemo_yamls directory. Run them and observe the batch time and checkpointing time. VAL_CHECK_INTERVAL determines how frequently the model is saved.

Takes about 30 min to start.

#### Real world - deepspeed
```
cd deepspeed
sky exec -c a100ds --cloud gcp --num-nodes 2 --env OUTPUT_PATH=/skystorage-mount/ deepspeed.yaml
```

We modify the SkyPilot deepspeed example to use deepspeed's model.save/load_checkpoint instead and provide timing information.
This updated main.py is uploaded to the VM as workdir and cp'd to the example directory.

Writes a checkpoint ~ 4GB in size.

#### Real world - VSCode SkyPilot Tutorial
Run `code --remote ssh-remote+a100 "/skystorage-mount"`

In VSCode terminal, run:
```bash
time git clone https://github.com/skypilot-org/skypilot-tutorial.git
```

Check if there are any errors.

Install jupyter and try using it (Don't use VSCode's Jupyter extension).
```bash
ssh -L 8888:localhost:8888 a100

pip install jupyterlab
cd /skystorage-mount/skypilot-tutorial
jupyter lab
```


#### Real world - Tensorboard logs
Run `sky launch -c a80 --env LOGS_DIR=/skystorage-mount/ tensorboard.yaml` to run tensorboard. See if the logs are being displayed correctly.

SSh port forward to access the tensorboard
```bash
ssh -L 6006:localhost:6006 a100
```
 Launch tensorboard
```bash
tensorboard --logdir=/juicefs/
```

Then open `localhost:6006` in your browser.



## Results


## GCSFuse with SkyStorage
### Synthetic

```console
(head, rank=0, pid=27079) ===== Benchmark Results =====
(head, rank=0, pid=27079) All results are reported as (bandwidth, IOPS)
(head, rank=0, pid=27079) 
(head, rank=0, pid=27079) ##### Sequential Read Results #####
(head, rank=0, pid=27079)       1344.70 MB/s    1282.40 IOPS
(head, rank=0, pid=27079) 
(head, rank=0, pid=27079) ##### Sequential Write Results #####
(head, rank=0, pid=27079)       3026.76 MB/s    2886.54 IOPS
(head, rank=0, pid=27079) 
(head, rank=0, pid=27079) ##### Small Files Read Results #####
(head, rank=0, pid=27079)       247.31 MB/s     235.85 IOPS
(head, rank=0, pid=27079) 
(head, rank=0, pid=27079) ##### Small Files Write Results #####
(head, rank=0, pid=27079)       3.72 MB/s       3.55 IOPS
```

### Real world - NeMo Read + Write (Checkpoint)
`sky launch -c a100nemo nemo_yamls/nemo_gpt_gcsfuse.yaml`

train step - 10.9s
checkpoint time - 4min 30s

```
task, pid=6678) Epoch 0: :   0%|          | 5/300000 [02:16<2281:40:05, v_num=0, reduced_train_loss=11.00, global_step=4.000, consumed_samples=960.0, train_step_timing in s=10.40, val_loss=11.00]Epoch 0, global step 5: 'val_loss' reached 10.95736 (best 10.95736), saving model to '/wiki/megatron_gpt--val_loss=10.96-step=5-consumed_samples=960.0.ckpt' as top 3
(task, pid=6678) [NeMo I 2024-07-30 23:01:58 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6678) [NeMo I 2024-07-30 23:12:27 nemo_model_checkpoint:226] New .nemo model saved to: /wiki/megatron_gpt.nemo

(task, pid=6678) [NeMo I 2024-07-30 23:29:10 nemo_model_checkpoint:299] /wiki/megatron_gpt.nemo already exists, moving existing checkpoint to /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:29:10 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6678) [NeMo I 2024-07-30 23:30:21 nemo_model_checkpoint:226] New .nemo model saved to: /wiki/megatron_gpt.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:30:21 nemo_model_checkpoint:228] Removing old .nemo backup /wiki/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 10/300000 [33:46<16886:15:22, v_num=0, reduced_train_loss=11.00, global_step=9.000, consumed_samples=1920.0, train_step_timing in s=10.90, val_loss=11.0
(task, pid=6678) Epoch 0: :   0%|          | 10/300000 [35:00<17500:51:47, v_num=0, reduced_train_loss=11.00, global_step=9.000, consumed_samples=1920.0, train_step_timing in s=10.90, val_loss=11.00]Epoch 0, global step 10: 'val_loss' reached 10.95736 (best 10.95736), saving model to '/wiki/megatron_gpt--val_loss=10.96-step=10-consumed_samples=1920.0.ckpt' as top 3
(task, pid=6678) [NeMo I 2024-07-30 23:34:39 nemo_model_checkpoint:299] /wiki/megatron_gpt.nemo already exists, moving existing checkpoint to /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:34:39 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6678) [NeMo I 2024-07-30 23:35:55 nemo_model_checkpoint:226] New .nemo model saved to: /wiki/megatron_gpt.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:35:55 nemo_model_checkpoint:228] Removing old .nemo backup /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:38:11 nemo_model_checkpoint:299] /wiki/megatron_gpt.nemo already exists, moving existing checkpoint to /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:38:11 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6678) [NeMo I 2024-07-30 23:39:23 nemo_model_checkpoint:226] New .nemo model saved to: /wiki/megatron_gpt.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:39:23 nemo_model_checkpoint:228] Removing old .nemo backup /wiki/megatron_gpt-v1.nemo
(task, pid=6678) Epoch 0: :   0%|          | 15/300000 [44:35<14860:53:36, v_num=0, reduced_train_loss=10.60, global_step=14.00, consumed_samples=2880.0, train_step_timing in s=8.440, val_loss=10.40]Epoch 0, global step 15: 'val_loss' reached 10.36892 (best 10.36892), saving model to '/wiki/megatron_gpt--val_loss=10.37-step=15-consumed_samples=2880.0.ckpt' as top 3
(task, pid=6678) [NeMo I 2024-07-30 23:44:14 nemo_model_checkpoint:299] /wiki/megatron_gpt.nemo already exists, moving existing checkpoint to /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:44:14 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6678) [NeMo I 2024-07-30 23:45:28 nemo_model_checkpoint:226] New .nemo model saved to: /wiki/megatron_gpt.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:45:28 nemo_model_checkpoint:228] Removing old .nemo backup /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:47:40 nemo_model_checkpoint:299] /wiki/megatron_gpt.nemo already exists, moving existing checkpoint to /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:47:41 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6678) [NeMo I 2024-07-30 23:48:48 nemo_model_checkpoint:226] New .nemo model saved to: /wiki/megatron_gpt.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:48:48 nemo_model_checkpoint:228] Removing old .nemo backup /wiki/megatron_gpt-v1.nemo
(task, pid=6678) Epoch 0: :   0%|          | 20/300000 [53:56<13485:46:11, v_num=0, reduced_train_loss=10.30, global_step=19.00, consumed_samples=3840.0, train_step_timing in s=8.710, val_loss=10.10]Epoch 0, global step 20: 'val_loss' reached 10.12600 (best 10.12600), saving model to '/wiki/megatron_gpt--val_loss=10.13-step=20-consumed_samples=3840.0.ckpt' as top 3
(task, pid=6678) [NeMo I 2024-07-30 23:53:36 nemo_model_checkpoint:299] /wiki/megatron_gpt.nemo already exists, moving existing checkpoint to /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:53:36 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6678) [NeMo I 2024-07-30 23:54:49 nemo_model_checkpoint:226] New .nemo model saved to: /wiki/megatron_gpt.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:54:49 nemo_model_checkpoint:228] Removing old .nemo backup /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:57:38 nemo_model_checkpoint:299] /wiki/megatron_gpt.nemo already exists, moving existing checkpoint to /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:57:38 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6678) [NeMo I 2024-07-30 23:58:48 nemo_model_checkpoint:226] New .nemo model saved to: /wiki/megatron_gpt.nemo
(task, pid=6678) [NeMo I 2024-07-30 23:58:48 nemo_model_checkpoint:228] Removing old .nemo backup /wiki/megatron_gpt-v1.nemo
(task, pid=6678) Epoch 0: :   0%|          | 25/300000 [1:04:04<12813:50:10, v_num=0, reduced_train_loss=9.830, global_step=24.00, consumed_samples=4800.0, train_step_timing in s=8.610, val_loss=9.740]Epoch 0, global step 25: 'val_loss' reached 9.73628 (best 9.73628), saving model to '/wiki/megatron_gpt--val_loss=9.74-step=25-consumed_samples=4800.0.ckpt' as top 3
(task, pid=6678) [NeMo I 2024-07-31 00:03:43 nemo_model_checkpoint:299] /wiki/megatron_gpt.nemo already exists, moving existing checkpoint to /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-31 00:03:44 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6678) [NeMo I 2024-07-31 00:04:57 nemo_model_checkpoint:226] New .nemo model saved to: /wiki/megatron_gpt.nemo
(task, pid=6678) [NeMo I 2024-07-31 00:04:57 nemo_model_checkpoint:228] Removing old .nemo backup /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-31 00:07:51 nemo_model_checkpoint:299] /wiki/megatron_gpt.nemo already exists, moving existing checkpoint to /wiki/megatron_gpt-v1.nemo
(task, pid=6678) [NeMo I 2024-07-31 00:07:52 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6678) [NeMo I 2024-07-31 00:09:02 nemo_model_checkpoint:226] New .nemo model saved to: /wiki/megatron_gpt.nemo
(task, pid=6678) [NeMo I 2024-07-31 00:09:02 nemo_model_checkpoint:228] Removing old .nemo backup /wiki/megatron_gpt-v1.nemo
(task, pid=6678) Validation DataLoader 0:  28%|██▊       | 448/1600 [00:20<00:53, 21.68it/s]
```

### Real world - VSCode SkyPilot Tutorial

Git clone took 3 min and failed. 
```
Cloning into 'skypilot-tutorial'...
çremote: Enumerating objects: 186, done.
remote: Counting objects: 100% (186/186), done.
remote: Compressing objects: 100% (121/121), done.
remote: Total 186 (delta 80), reused 161 (delta 59), pack-reused 0
warning: unable to access '/skystorage-mount/skypilot-tutorial/.git/info/grafts': Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/80: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/dd: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/47: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/90: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/1b: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/9c: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/b6: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/40: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/ec: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/3c: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/19: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/9e: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/81: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/70: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/05: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/c4: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/0a: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/a6: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/0f: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/4e: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/5f: Interrupted system call
error: unable to open /skystorage-mount/skypilot-tutorial/.git/objects/fb: Interrupted system call
Receiving objects: 100% (186/186), 62.28 KiB | 2.00 KiB/s, done.
Resolving deltas: 100% (80/80), done.
warning: unable to access '/skystorage-mount/skypilot-tutorial/.git/info/attributes': Interrupted system call
error: unable to create file 01_hello_sky/01_hello_sky.ipynb: Interrupted system call
error: unable to create file 03_spot_instances/03_spot_instances.ipynb: Interrupted system call
error: unable to create file 03_spot_instances/bert.yaml: Interrupted system call
error: unable to create file 03_spot_instances/terminator.py: Interrupted system call
error: unable to create file Dockerfile: Interrupted system call
Updating files: 100% (15/15), done.
fatal: unable to checkout working tree
warning: Clone succeeded, but checkout failed.
You can inspect what was checked out with 'git status'
and retry with 'git restore --source=HEAD :/'


real    3m12.783s
user    0m0.096s
sys     0m0.072s
```

git commands also take very long. E.g., `git status` took 21s

Even though the git clone failed, the files were still present in the directory.

`jupyter lab` worked and I was able to edit files.


### Real world - Tensorboard logs

Directories are created, but logs are not written. Logs showed up after running `sky cancel`



## GCS with Rclone VFS Full Cache
### Synthetic
`sky exec -c a100 --env BENCH_PATH=/rclone_mount synthetic_benchmarks.yaml`

```console
(storage-demo, pid=12659) All results are reported as (bandwidth, IOPS)
(storage-demo, pid=12659) 
(storage-demo, pid=12659) ##### Sequential Read Results #####
(storage-demo, pid=12659)       1877.17 MB/s    1790.21 IOPS
(storage-demo, pid=12659) 
(storage-demo, pid=12659) ##### Sequential Write Results #####
(storage-demo, pid=12659)       1947.83 MB/s    1857.60 IOPS
(storage-demo, pid=12659) 
(storage-demo, pid=12659) ##### Small Files Read Results #####
(storage-demo, pid=12659)       1081.01 MB/s    1030.93 IOPS
(storage-demo, pid=12659) 
(storage-demo, pid=12659) ##### Small Files Write Results #####
(storage-demo, pid=12659)       620.46 MB/s     591.72 IOPS
```

### Real world - NeMo Read + Write (Checkpoint)
`sky launch -c a100nemo nemo_yamls/nemo_gpt_rclone.yaml`

You may need to manually mount, the script is a bit buggy.

Bad at consistency - cannot be used for parallel training.

Train step - 4.5s
Checkpoint time - 30s

```
(task, pid=6679) Epoch 0: :   0%|          | 5/300000 [01:59<1989:26:47, v_num=1, reduced_train_loss=11.00, global_step=4.000, consumed_samples=960.0, train_step_timing in s=4.560, val_loss=11.00]Epoch 0, global step 5: 'val_loss' reached 10.95736 (best 10.95736), saving model to '/rclone_mount/megatron_gpt--val_loss=10.96-step=5-consumed_samples=960.0.ckpt' as top 3
(task, pid=6679) [NeMo I 2024-07-31 01:02:07 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:02:17 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:02:26 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:02:26 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:02:39 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:02:39 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) Epoch 0: :   0%|          | 6/300000 [02:44<2283:45:02, v_num=1, reduced_train_loss=11.00, global_step=5.000, consumed_samples=1152.0, train_step_timing in s=4.580(task, pid=6679) Epoch 0: :   0%|          | 7/300000 [02:48<2011:48:34, v_num=1, reduced_train_loss=11.00, global_step=6.000, consumed_samples=1344.0, train_step_timing in s=4.560(task, pid=6679) Epoch 0: :   0%|          | 8/300000 [02:53<1807:51:22, v_num=1, reduced_train_loss=11.00, global_step=7.000, consumed_samples=1536.0, train_step_timing in s=4.560(task, pid=6679) Epoch 0: :   0%|          | 9/300000 [02:58<1649:14:19, v_num=1, reduced_train_loss=11.00, global_step=8.000, consumed_samples=1728.0, train_step_timing in s=4.560Epoch 0: :   0%|          | 10/300000 [03:02<1522:28:00, v_num=1, reduced_train_loss=11.00, global_step=8.000, consumed_samples=1728.0, train_step_timing in s=4.560, val_loss=11.00Epoch 0: :   0%|          | 10/300000 [03:02<1522:28:16, v_num=1, reduced_train_loss=11.00, global_step=9.000, consumed_samples=1920.0, train_step_timing in s=4.580, val_loss=11.00
(task, pid=6679) Epoch 0: :   0%|          | 10/300000 [04:16<2137:38:01, v_num=1, reduced_train_loss=11.00, global_step=9.000, consumed_samples=1920.0, train_step_timing in s=4.580, val_loss=11.00]Epoch 0, global step 10: 'val_loss' reached 10.95736 (best 10.95736), saving model to '/rclone_mount/megatron_gpt--val_loss=10.96-step=10-consumed_samples=1920.0.ckpt' as top 3
(task, pid=6679) [NeMo I 2024-07-31 01:04:24 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:04:24 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:04:34 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:04:34 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:04:43 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:04:44 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:04:55 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:04:55 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 11/300000 [05:15<2391:17:07, v_num=1, reduced_train_loss=11.00, global_step=9.000, consumed_samples=1920.0, train_step_timing in s=4.580, val_loss=11.00(task, pid=6679) Epoch 0: :   0%|          | 11/300000 [05:15<2391:17:21, v_num=1, reduced_train_loss=11.00, global_step=10.00, consumed_samples=2112.0, train_step_timing in s=4.58Epoch 0: :   0%|          | 12/300000 [05:20<2223:42:18, v_num=1, reduced_train_loss=11.00, global_step=10.00, consumed_samples=2112.0, train_step_timing in s=4.580, val_loss=11.00(task, pid=6679) Epoch 0: :   0%|          | 12/300000 [05:20<2223:42:30, v_num=1, reduced_train_loss=10.90, global_step=11.00, consumed_samples=2304.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 13/300000 [05:24<2081:57:52, v_num=1, reduced_train_loss=10.90, global_step=11.00, consumed_samples=2304.0, train_step_timing in s=4.560, val_loss=11.00(task, pid=6679) Epoch 0: :   0%|          | 13/300000 [05:24<2081:58:04, v_num=1, reduced_train_loss=10.80, global_step=12.00, consumed_samples=2496.0, train_step_timing in s=4.57Epoch 0: :   0%|          | 14/300000 [05:29<1960:25:03, v_num=1, reduced_train_loss=10.80, global_step=12.00, consumed_samples=2496.0, train_step_timing in s=4.570, val_loss=11.00(task, pid=6679) Epoch 0: :   0%|          | 14/300000 [05:29<1960:25:14, v_num=1, reduced_train_loss=10.70, global_step=13.00, consumed_samples=2688.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 15/300000 [05:33<1855:06:32, v_num=1, reduced_train_loss=10.70, global_step=13.00, consumed_samples=2688.0, train_step_timing in s=4.560, val_loss=11.00(task, pid=6679) Epoch 0: :   0%|          | 15/300000 [05:33<1855:06:42, v_num=1, reduced_train_loss=10.60, global_step=14.00, consumed_samples=2880.0, train_step_timing in s=4.57
Epoch 0: :   0%|          | 15/300000 [06:47<2264:46:01, v_num=1, reduced_train_loss=10.60, global_step=14.00, consumed_samples=2880.0, train_step_timing in s=4.570, val_loss=10.40]Epoch 0, global step 15: 'val_loss' reached 10.36892 (best 10.36892), saving model to '/rclone_mount/megatron_gpt--val_loss=10.37-step=15-consumed_samples=2880.0.ckpt' as top 3
(task, pid=6679) [NeMo I 2024-07-31 01:06:55 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:06:56 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:07:05 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:07:05 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:07:16 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:07:16 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:07:27 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:07:27 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 16/300000 [07:47<2435:07:39, v_num=1, reduced_train_loss=10.60, global_step=14.00, consumed_samples=2880.0, train_step_timing in s=4.570, val_loss=10.40(task, pid=6679) Epoch 0: :   0%|          | 16/300000 [07:47<2435:07:48, v_num=1, reduced_train_loss=10.40, global_step=15.00, consumed_samples=3072.0, train_step_timing in s=4.57Epoch 0: :   0%|          | 17/300000 [07:52<2314:15:05, v_num=1, reduced_train_loss=10.40, global_step=15.00, consumed_samples=3072.0, train_step_timing in s=4.570, val_loss=10.40(task, pid=6679) Epoch 0: :   0%|          | 17/300000 [07:52<2314:15:14, v_num=1, reduced_train_loss=10.40, global_step=16.00, consumed_samples=3264.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 18/300000 [07:56<2206:47:33, v_num=1, reduced_train_loss=10.40, global_step=16.00, consumed_samples=3264.0, train_step_timing in s=4.560, val_loss=10.40(task, pid=6679) Epoch 0: :   0%|          | 18/300000 [07:56<2206:47:42, v_num=1, reduced_train_loss=10.30, global_step=17.00, consumed_samples=3456.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 19/300000 [08:01<2110:38:49, v_num=1, reduced_train_loss=10.30, global_step=17.00, consumed_samples=3456.0, train_step_timing in s=4.560, val_loss=10.40(task, pid=6679) Epoch 0: :   0%|          | 19/300000 [08:01<2110:38:57, v_num=1, reduced_train_loss=10.30, global_step=18.00, consumed_samples=3648.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 20/300000 [08:06<2025:07:33, v_num=1, reduced_train_loss=10.30, global_step=18.00, consumed_samples=3648.0, train_step_timing in s=4.560, val_loss=10.40Epoch 0: :   0%|          | 20/300000 [08:06<2025:07:41, v_num=1, reduced_train_loss=10.30, global_step=19.00, consumed_samples=3840.0, train_step_timing in s=4.800, val_loss=10.40
(task, pid=6679) Epoch 0: :   0%|          | 20/300000 [09:19<2332:34:42, v_num=1, reduced_train_loss=10.30, global_step=19.00, consumed_samples=3840.0, train_step_timing in s=4.800, val_loss=10.10]Epoch 0, global step 20: 'val_loss' reached 10.12600 (best 10.12600), saving model to '/rclone_mount/megatron_gpt--val_loss=10.13-step=20-consumed_samples=3840.0.ckpt' as top 3
(task, pid=6679) [NeMo I 2024-07-31 01:09:28 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:09:28 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:09:38 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:09:38 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:10:02 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:10:02 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:10:12 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:10:12 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 21/300000 [10:33<2513:27:59, v_num=1, reduced_train_loss=10.30, global_step=19.00, consumed_samples=3840.0, train_step_timing in s=4.800, val_loss=10.10(task, pid=6679) Epoch 0: :   0%|          | 21/300000 [10:33<2513:28:08, v_num=1, reduced_train_loss=10.20, global_step=20.00, consumed_samples=4032.0, train_step_timing in s=4.58Epoch 0: :   0%|          | 22/300000 [10:38<2416:29:59, v_num=1, reduced_train_loss=10.20, global_step=20.00, consumed_samples=4032.0, train_step_timing in s=4.580, val_loss=10.10(task, pid=6679) Epoch 0: :   0%|          | 22/300000 [10:38<2416:30:06, v_num=1, reduced_train_loss=10.10, global_step=21.00, consumed_samples=4224.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 23/300000 [10:42<2327:59:11, v_num=1, reduced_train_loss=10.10, global_step=21.00, consumed_samples=4224.0, train_step_timing in s=4.560, val_loss=10.10(task, pid=6679) Epoch 0: :   0%|          | 23/300000 [10:42<2327:59:17, v_num=1, reduced_train_loss=9.990, global_step=22.00, consumed_samples=4416.0, train_step_timing in s=4.57Epoch 0: :   0%|          | 24/300000 [10:47<2246:49:33, v_num=1, reduced_train_loss=9.990, global_step=22.00, consumed_samples=4416.0, train_step_timing in s=4.570, val_loss=10.10(task, pid=6679) Epoch 0: :   0%|          | 24/300000 [10:47<2246:49:39, v_num=1, reduced_train_loss=9.920, global_step=23.00, consumed_samples=4608.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 25/300000 [10:51<2172:09:37, v_num=1, reduced_train_loss=9.920, global_step=23.00, consumed_samples=4608.0, train_step_timing in s=4.560, val_loss=10.10Epoch 0: :   0%|          | 25/300000 [10:51<2172:09:44, v_num=1, reduced_train_loss=9.830, global_step=24.00, consumed_samples=4800.0, train_step_timing in s=4.560, val_loss=10.10
Epoch 0: :   0%|          | 25/300000 [12:05<2418:23:46, v_num=1, reduced_train_loss=9.830, global_step=24.00, consumed_samples=4800.0, train_step_timing in s=4.560, val_loss=9.740]Epoch 0, global step 25: 'val_loss' reached 9.73628 (best 9.73628), saving model to '/rclone_mount/megatron_gpt--val_loss=9.74-step=25-consumed_samples=4800.0.ckpt' as top 3
(task, pid=6679) [NeMo I 2024-07-31 01:12:13 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:12:13 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:12:23 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:12:23 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:12:48 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:12:48 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:12:58 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:12:58 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 26/300000 [13:18<2560:20:19, v_num=1, reduced_train_loss=9.830, global_step=24.00, consumed_samples=4800.0, train_step_timing in s=4.560, val_loss=9.740(task, pid=6679) Epoch 0: :   0%|          | 26/300000 [13:18<2560:20:31, v_num=1, reduced_train_loss=9.790, global_step=25.00, consumed_samples=4992.0, train_step_timing in s=4.59Epoch 0: :   0%|          | 27/300000 [13:23<2479:36:44, v_num=1, reduced_train_loss=9.790, global_step=25.00, consumed_samples=4992.0, train_step_timing in s=4.590, val_loss=9.740(task, pid=6679) Epoch 0: :   0%|          | 27/300000 [13:23<2479:36:49, v_num=1, reduced_train_loss=9.740, global_step=26.00, consumed_samples=5184.0, train_step_timing in s=4.57Epoch 0: :   0%|          | 28/300000 [13:28<2404:38:11, v_num=1, reduced_train_loss=9.740, global_step=26.00, consumed_samples=5184.0, train_step_timing in s=4.570, val_loss=9.740(task, pid=6679) Epoch 0: :   0%|          | 28/300000 [13:28<2404:38:18, v_num=1, reduced_train_loss=9.700, global_step=27.00, consumed_samples=5376.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 29/300000 [13:32<2334:49:57, v_num=1, reduced_train_loss=9.700, global_step=27.00, consumed_samples=5376.0, train_step_timing in s=4.560, val_loss=9.740(task, pid=6679) Epoch 0: :   0%|          | 29/300000 [13:32<2334:50:03, v_num=1, reduced_train_loss=9.640, global_step=28.00, consumed_samples=5568.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 30/300000 [13:37<2269:40:30, v_num=1, reduced_train_loss=9.640, global_step=28.00, consumed_samples=5568.0, train_step_timing in s=4.560, val_loss=9.740Epoch 0: :   0%|          | 30/300000 [13:37<2269:40:35, v_num=1, reduced_train_loss=9.620, global_step=29.00, consumed_samples=5760.0, train_step_timing in s=4.560, val_loss=9.740]
(task, pid=6679) Validation DataLoader 0: 
```

### Real world - VSCode SkyPilot Tutorial


```
(base) root@a100nemo-2ea4-head-6fr3jme2-compute:/rclone_mount# time git clone https://github.com/skypilot-org/skypilot-tutorial.git
Cloning into 'skypilot-tutorial'...
remote: Enumerating objects: 186, done.
remote: Counting objects: 100% (186/186), done.
remote: Compressing objects: 100% (121/121), done.
remote: Total 186 (delta 80), reused 161 (delta 59), pack-reused 0
Receiving objects: 100% (186/186), 62.28 KiB | 777.00 KiB/s, done.
Resolving deltas: 100% (80/80), done.

real    0m1.427s
user    0m0.084s
sys     0m0.097s
```

Notebooks work.

### Real world - Tensorboard logs

Tensorboard works fine. Cannot be accessed remotely - logs are written only after job completes.



## GCS with Rclone VFS Full Cache
### Synthetic
`sky exec -c a100 --env BENCH_PATH=/rclone_mount synthetic_benchmarks.yaml`

```console
(storage-demo, pid=12659) All results are reported as (bandwidth, IOPS)
(storage-demo, pid=12659) 
(storage-demo, pid=12659) ##### Sequential Read Results #####
(storage-demo, pid=12659)       1877.17 MB/s    1790.21 IOPS
(storage-demo, pid=12659) 
(storage-demo, pid=12659) ##### Sequential Write Results #####
(storage-demo, pid=12659)       1947.83 MB/s    1857.60 IOPS
(storage-demo, pid=12659) 
(storage-demo, pid=12659) ##### Small Files Read Results #####
(storage-demo, pid=12659)       1081.01 MB/s    1030.93 IOPS
(storage-demo, pid=12659) 
(storage-demo, pid=12659) ##### Small Files Write Results #####
(storage-demo, pid=12659)       620.46 MB/s     591.72 IOPS
```

### Real world - NeMo Read + Write (Checkpoint)
`sky launch -c a100nemo nemo_yamls/nemo_gpt_rclone.yaml`

You may need to manually mount, the script is a bit buggy.

Bad at consistency - cannot be used for parallel training.

Train step - 4.5s
Checkpoint time - 30s

```
(task, pid=6679) Epoch 0: :   0%|          | 5/300000 [01:59<1989:26:47, v_num=1, reduced_train_loss=11.00, global_step=4.000, consumed_samples=960.0, train_step_timing in s=4.560, val_loss=11.00]Epoch 0, global step 5: 'val_loss' reached 10.95736 (best 10.95736), saving model to '/rclone_mount/megatron_gpt--val_loss=10.96-step=5-consumed_samples=960.0.ckpt' as top 3
(task, pid=6679) [NeMo I 2024-07-31 01:02:07 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:02:17 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:02:26 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:02:26 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:02:39 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:02:39 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) Epoch 0: :   0%|          | 6/300000 [02:44<2283:45:02, v_num=1, reduced_train_loss=11.00, global_step=5.000, consumed_samples=1152.0, train_step_timing in s=4.580(task, pid=6679) Epoch 0: :   0%|          | 7/300000 [02:48<2011:48:34, v_num=1, reduced_train_loss=11.00, global_step=6.000, consumed_samples=1344.0, train_step_timing in s=4.560(task, pid=6679) Epoch 0: :   0%|          | 8/300000 [02:53<1807:51:22, v_num=1, reduced_train_loss=11.00, global_step=7.000, consumed_samples=1536.0, train_step_timing in s=4.560(task, pid=6679) Epoch 0: :   0%|          | 9/300000 [02:58<1649:14:19, v_num=1, reduced_train_loss=11.00, global_step=8.000, consumed_samples=1728.0, train_step_timing in s=4.560Epoch 0: :   0%|          | 10/300000 [03:02<1522:28:00, v_num=1, reduced_train_loss=11.00, global_step=8.000, consumed_samples=1728.0, train_step_timing in s=4.560, val_loss=11.00Epoch 0: :   0%|          | 10/300000 [03:02<1522:28:16, v_num=1, reduced_train_loss=11.00, global_step=9.000, consumed_samples=1920.0, train_step_timing in s=4.580, val_loss=11.00
(task, pid=6679) Epoch 0: :   0%|          | 10/300000 [04:16<2137:38:01, v_num=1, reduced_train_loss=11.00, global_step=9.000, consumed_samples=1920.0, train_step_timing in s=4.580, val_loss=11.00]Epoch 0, global step 10: 'val_loss' reached 10.95736 (best 10.95736), saving model to '/rclone_mount/megatron_gpt--val_loss=10.96-step=10-consumed_samples=1920.0.ckpt' as top 3
(task, pid=6679) [NeMo I 2024-07-31 01:04:24 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:04:24 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:04:34 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:04:34 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:04:43 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:04:44 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:04:55 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:04:55 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 11/300000 [05:15<2391:17:07, v_num=1, reduced_train_loss=11.00, global_step=9.000, consumed_samples=1920.0, train_step_timing in s=4.580, val_loss=11.00(task, pid=6679) Epoch 0: :   0%|          | 11/300000 [05:15<2391:17:21, v_num=1, reduced_train_loss=11.00, global_step=10.00, consumed_samples=2112.0, train_step_timing in s=4.58Epoch 0: :   0%|          | 12/300000 [05:20<2223:42:18, v_num=1, reduced_train_loss=11.00, global_step=10.00, consumed_samples=2112.0, train_step_timing in s=4.580, val_loss=11.00(task, pid=6679) Epoch 0: :   0%|          | 12/300000 [05:20<2223:42:30, v_num=1, reduced_train_loss=10.90, global_step=11.00, consumed_samples=2304.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 13/300000 [05:24<2081:57:52, v_num=1, reduced_train_loss=10.90, global_step=11.00, consumed_samples=2304.0, train_step_timing in s=4.560, val_loss=11.00(task, pid=6679) Epoch 0: :   0%|          | 13/300000 [05:24<2081:58:04, v_num=1, reduced_train_loss=10.80, global_step=12.00, consumed_samples=2496.0, train_step_timing in s=4.57Epoch 0: :   0%|          | 14/300000 [05:29<1960:25:03, v_num=1, reduced_train_loss=10.80, global_step=12.00, consumed_samples=2496.0, train_step_timing in s=4.570, val_loss=11.00(task, pid=6679) Epoch 0: :   0%|          | 14/300000 [05:29<1960:25:14, v_num=1, reduced_train_loss=10.70, global_step=13.00, consumed_samples=2688.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 15/300000 [05:33<1855:06:32, v_num=1, reduced_train_loss=10.70, global_step=13.00, consumed_samples=2688.0, train_step_timing in s=4.560, val_loss=11.00(task, pid=6679) Epoch 0: :   0%|          | 15/300000 [05:33<1855:06:42, v_num=1, reduced_train_loss=10.60, global_step=14.00, consumed_samples=2880.0, train_step_timing in s=4.57
Epoch 0: :   0%|          | 15/300000 [06:47<2264:46:01, v_num=1, reduced_train_loss=10.60, global_step=14.00, consumed_samples=2880.0, train_step_timing in s=4.570, val_loss=10.40]Epoch 0, global step 15: 'val_loss' reached 10.36892 (best 10.36892), saving model to '/rclone_mount/megatron_gpt--val_loss=10.37-step=15-consumed_samples=2880.0.ckpt' as top 3
(task, pid=6679) [NeMo I 2024-07-31 01:06:55 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:06:56 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:07:05 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:07:05 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:07:16 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:07:16 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:07:27 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:07:27 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 16/300000 [07:47<2435:07:39, v_num=1, reduced_train_loss=10.60, global_step=14.00, consumed_samples=2880.0, train_step_timing in s=4.570, val_loss=10.40(task, pid=6679) Epoch 0: :   0%|          | 16/300000 [07:47<2435:07:48, v_num=1, reduced_train_loss=10.40, global_step=15.00, consumed_samples=3072.0, train_step_timing in s=4.57Epoch 0: :   0%|          | 17/300000 [07:52<2314:15:05, v_num=1, reduced_train_loss=10.40, global_step=15.00, consumed_samples=3072.0, train_step_timing in s=4.570, val_loss=10.40(task, pid=6679) Epoch 0: :   0%|          | 17/300000 [07:52<2314:15:14, v_num=1, reduced_train_loss=10.40, global_step=16.00, consumed_samples=3264.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 18/300000 [07:56<2206:47:33, v_num=1, reduced_train_loss=10.40, global_step=16.00, consumed_samples=3264.0, train_step_timing in s=4.560, val_loss=10.40(task, pid=6679) Epoch 0: :   0%|          | 18/300000 [07:56<2206:47:42, v_num=1, reduced_train_loss=10.30, global_step=17.00, consumed_samples=3456.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 19/300000 [08:01<2110:38:49, v_num=1, reduced_train_loss=10.30, global_step=17.00, consumed_samples=3456.0, train_step_timing in s=4.560, val_loss=10.40(task, pid=6679) Epoch 0: :   0%|          | 19/300000 [08:01<2110:38:57, v_num=1, reduced_train_loss=10.30, global_step=18.00, consumed_samples=3648.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 20/300000 [08:06<2025:07:33, v_num=1, reduced_train_loss=10.30, global_step=18.00, consumed_samples=3648.0, train_step_timing in s=4.560, val_loss=10.40Epoch 0: :   0%|          | 20/300000 [08:06<2025:07:41, v_num=1, reduced_train_loss=10.30, global_step=19.00, consumed_samples=3840.0, train_step_timing in s=4.800, val_loss=10.40
(task, pid=6679) Epoch 0: :   0%|          | 20/300000 [09:19<2332:34:42, v_num=1, reduced_train_loss=10.30, global_step=19.00, consumed_samples=3840.0, train_step_timing in s=4.800, val_loss=10.10]Epoch 0, global step 20: 'val_loss' reached 10.12600 (best 10.12600), saving model to '/rclone_mount/megatron_gpt--val_loss=10.13-step=20-consumed_samples=3840.0.ckpt' as top 3
(task, pid=6679) [NeMo I 2024-07-31 01:09:28 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:09:28 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:09:38 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:09:38 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:10:02 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:10:02 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:10:12 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:10:12 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 21/300000 [10:33<2513:27:59, v_num=1, reduced_train_loss=10.30, global_step=19.00, consumed_samples=3840.0, train_step_timing in s=4.800, val_loss=10.10(task, pid=6679) Epoch 0: :   0%|          | 21/300000 [10:33<2513:28:08, v_num=1, reduced_train_loss=10.20, global_step=20.00, consumed_samples=4032.0, train_step_timing in s=4.58Epoch 0: :   0%|          | 22/300000 [10:38<2416:29:59, v_num=1, reduced_train_loss=10.20, global_step=20.00, consumed_samples=4032.0, train_step_timing in s=4.580, val_loss=10.10(task, pid=6679) Epoch 0: :   0%|          | 22/300000 [10:38<2416:30:06, v_num=1, reduced_train_loss=10.10, global_step=21.00, consumed_samples=4224.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 23/300000 [10:42<2327:59:11, v_num=1, reduced_train_loss=10.10, global_step=21.00, consumed_samples=4224.0, train_step_timing in s=4.560, val_loss=10.10(task, pid=6679) Epoch 0: :   0%|          | 23/300000 [10:42<2327:59:17, v_num=1, reduced_train_loss=9.990, global_step=22.00, consumed_samples=4416.0, train_step_timing in s=4.57Epoch 0: :   0%|          | 24/300000 [10:47<2246:49:33, v_num=1, reduced_train_loss=9.990, global_step=22.00, consumed_samples=4416.0, train_step_timing in s=4.570, val_loss=10.10(task, pid=6679) Epoch 0: :   0%|          | 24/300000 [10:47<2246:49:39, v_num=1, reduced_train_loss=9.920, global_step=23.00, consumed_samples=4608.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 25/300000 [10:51<2172:09:37, v_num=1, reduced_train_loss=9.920, global_step=23.00, consumed_samples=4608.0, train_step_timing in s=4.560, val_loss=10.10Epoch 0: :   0%|          | 25/300000 [10:51<2172:09:44, v_num=1, reduced_train_loss=9.830, global_step=24.00, consumed_samples=4800.0, train_step_timing in s=4.560, val_loss=10.10
Epoch 0: :   0%|          | 25/300000 [12:05<2418:23:46, v_num=1, reduced_train_loss=9.830, global_step=24.00, consumed_samples=4800.0, train_step_timing in s=4.560, val_loss=9.740]Epoch 0, global step 25: 'val_loss' reached 9.73628 (best 9.73628), saving model to '/rclone_mount/megatron_gpt--val_loss=9.74-step=25-consumed_samples=4800.0.ckpt' as top 3
(task, pid=6679) [NeMo I 2024-07-31 01:12:13 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:12:13 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:12:23 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:12:23 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:12:48 nemo_model_checkpoint:299] /rclone_mount/megatron_gpt.nemo already exists, moving existing checkpoint to /rclone_mount/megatron_gpt-v1.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:12:48 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(task, pid=6679) [NeMo I 2024-07-31 01:12:58 nemo_model_checkpoint:226] New .nemo model saved to: /rclone_mount/megatron_gpt.nemo
(task, pid=6679) [NeMo I 2024-07-31 01:12:58 nemo_model_checkpoint:228] Removing old .nemo backup /rclone_mount/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 26/300000 [13:18<2560:20:19, v_num=1, reduced_train_loss=9.830, global_step=24.00, consumed_samples=4800.0, train_step_timing in s=4.560, val_loss=9.740(task, pid=6679) Epoch 0: :   0%|          | 26/300000 [13:18<2560:20:31, v_num=1, reduced_train_loss=9.790, global_step=25.00, consumed_samples=4992.0, train_step_timing in s=4.59Epoch 0: :   0%|          | 27/300000 [13:23<2479:36:44, v_num=1, reduced_train_loss=9.790, global_step=25.00, consumed_samples=4992.0, train_step_timing in s=4.590, val_loss=9.740(task, pid=6679) Epoch 0: :   0%|          | 27/300000 [13:23<2479:36:49, v_num=1, reduced_train_loss=9.740, global_step=26.00, consumed_samples=5184.0, train_step_timing in s=4.57Epoch 0: :   0%|          | 28/300000 [13:28<2404:38:11, v_num=1, reduced_train_loss=9.740, global_step=26.00, consumed_samples=5184.0, train_step_timing in s=4.570, val_loss=9.740(task, pid=6679) Epoch 0: :   0%|          | 28/300000 [13:28<2404:38:18, v_num=1, reduced_train_loss=9.700, global_step=27.00, consumed_samples=5376.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 29/300000 [13:32<2334:49:57, v_num=1, reduced_train_loss=9.700, global_step=27.00, consumed_samples=5376.0, train_step_timing in s=4.560, val_loss=9.740(task, pid=6679) Epoch 0: :   0%|          | 29/300000 [13:32<2334:50:03, v_num=1, reduced_train_loss=9.640, global_step=28.00, consumed_samples=5568.0, train_step_timing in s=4.56Epoch 0: :   0%|          | 30/300000 [13:37<2269:40:30, v_num=1, reduced_train_loss=9.640, global_step=28.00, consumed_samples=5568.0, train_step_timing in s=4.560, val_loss=9.740Epoch 0: :   0%|          | 30/300000 [13:37<2269:40:35, v_num=1, reduced_train_loss=9.620, global_step=29.00, consumed_samples=5760.0, train_step_timing in s=4.560, val_loss=9.740]
(task, pid=6679) Validation DataLoader 0: 
```

### Real world - Deepspeed

Takes 12.1s to write ~4GB checkpoint, but lack of consistency breaks things.

The `latest` file (a text file containing the tag of the known healthy checkpoint) is uploaded earlier than the actual checkpoint uploads, which causes restore to fail if the node is preempted at the wrong time. 

Adding --transfers=1 to rclone mounting command helps only if the checkpoints are not too frequent. If checkpointed very frequently, it just keeps uploading the checkpoint files and never writes the `latest` file since it keeps getting a new modtime.



### Real world - VSCode SkyPilot Tutorial


```
(base) root@a100nemo-2ea4-head-6fr3jme2-compute:/rclone_mount# time git clone https://github.com/skypilot-org/skypilot-tutorial.git
Cloning into 'skypilot-tutorial'...
remote: Enumerating objects: 186, done.
remote: Counting objects: 100% (186/186), done.
remote: Compressing objects: 100% (121/121), done.
remote: Total 186 (delta 80), reused 161 (delta 59), pack-reused 0
Receiving objects: 100% (186/186), 62.28 KiB | 777.00 KiB/s, done.
Resolving deltas: 100% (80/80), done.

real    0m1.427s
user    0m0.084s
sys     0m0.097s
```

Notebooks work.

### Real world - Tensorboard logs

Tensorboard works fine. Cannot be accessed remotely - logs are written only after job completes.


## GCS with JuiceFS
### Synthetic
`sky exec -c a100 --env BENCH_PATH=/testmnt synthetic_benchmarks.yaml`

```console
(storage-demo, pid=19893) ===== Benchmark Results =====
(storage-demo, pid=19893) All results are reported as (bandwidth, IOPS)
(storage-demo, pid=19893) 
(storage-demo, pid=19893) ##### Sequential Read Results #####
(storage-demo, pid=19893)       322.06 MB/s     307.14 IOPS
(storage-demo, pid=19893) 
(storage-demo, pid=19893) ##### Sequential Write Results #####
(storage-demo, pid=19893)       365.53 MB/s     348.60 IOPS
(storage-demo, pid=19893) 
(storage-demo, pid=19893) ##### Small Files Read Results #####
(storage-demo, pid=19893)       455.90 MB/s     434.78 IOPS
(storage-demo, pid=19893) 
(storage-demo, pid=19893) ##### Small Files Write Results #####
(storage-demo, pid=19893)       340.45 MB/s     324.68 IOPS
```

===== CROSS REGION ======
VM in us-central1, postgres and GCS and in me-west1. 
```
(storage-demo, pid=5260) ##### Sequential Read Results #####
(storage-demo, pid=5260)        120.22 MB/s     114.65 IOPS
(storage-demo, pid=5260) 
(storage-demo, pid=5260) ##### Sequential Write Results #####
(storage-demo, pid=5260)        38.05 MB/s      36.29 IOPS
(storage-demo, pid=5260) 
(storage-demo, pid=5260) ##### Small Files Read Results #####
(storage-demo, pid=5260)        29.01 MB/s      27.67 IOPS
(storage-demo, pid=5260) 
(storage-demo, pid=5260) ##### Small Files Write Results #####
(storage-demo, pid=5260)        1.87 MB/s       1.78 IOPS
```

Tuned with --buffer-size=5000 --max-uploads=50
```
(head, rank=0, pid=5971) ===== Benchmark Results =====
(head, rank=0, pid=5971) All results are reported as (bandwidth, IOPS)
(head, rank=0, pid=5971) 
(head, rank=0, pid=5971) ##### Sequential Read Results #####
(head, rank=0, pid=5971)        313.73 MB/s     299.20 IOPS
(head, rank=0, pid=5971) 
(head, rank=0, pid=5971) ##### Sequential Write Results #####
(head, rank=0, pid=5971)        3693.01 MB/s    3521.93 IOPS
(head, rank=0, pid=5971) 
(head, rank=0, pid=5971) ##### Small Files Read Results #####
(head, rank=0, pid=5971)        519.10 MB/s     495.05 IOPS
(head, rank=0, pid=5971) 
(head, rank=0, pid=5971) ##### Small Files Write Results #####
(head, rank=0, pid=5971)        212.26 MB/s     202.43 IOPS
```

### Real world - NeMo Read + Write (Checkpoint)
`sky launch -c a100nemo nemo_yamls/nemo_gpt_juicefs.yaml`

```
Epoch 0: :   0%|          | 5/300000 [01:50<1836:56:45, v_num=0, reduced_train_loss=11.00, global_step=4.000, consumed_samples=960.0, train_step_timing in s=4.680, val_loss=11.00]Epoch 0, global step 5: 'val_loss' reached 10.95736 (best 10.95736), saving model to '/juicefs/megatron_gpt--val_loss=10.96-step=5-consumed_samples=960.0.ckpt' as top 30it/s]
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:25:15 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:26:00 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:27:23 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:27:23 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:28:07 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:28:07 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 10/300000 [07:41<3844:42:33, v_num=0, reduced_train_loss=11.00, global_step=9.000, consumed_samples=1920.0, train_step_timing in s=4.610, val_loss=11.00]Epoch 0, global step 10: 'val_loss' reached 10.95736 (best 10.95736), saving model to '/juicefs/megatron_gpt--val_loss=10.96-step=10-consumed_samples=1920.0.ckpt' as top 3]
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:31:06 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:31:06 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:31:53 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:31:53 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:33:15 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:33:15 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:34:00 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:34:00 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 15/300000 [13:49<4608:19:40, v_num=0, reduced_train_loss=10.60, global_step=14.00, consumed_samples=2880.0, train_step_timing in s=4.600, val_loss=10.40]Epoch 0, global step 15: 'val_loss' reached 10.36892 (best 10.36892), saving model to '/juicefs/megatron_gpt--val_loss=10.37-step=15-consumed_samples=2880.0.ckpt' as top 3]
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:37:14 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:37:14 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:38:03 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5753) [NeMo I 2024-07-31 23:38:03 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
```

Tuned with --buffer-size=5000 --max-uploads=50
```
(head, rank=0, pid=5969) Epoch 0: :   0%|          | 15/300000 [15:33<5188:20:56, v_num=0, reduced_train_loss=10.60, global_step=14.00, consumed_samples=2880.0, train_step_timing in s=4.600, val_loss=10.40]Epoch 0, global step 15: 'val_loss' reached 10.36892 (best 10.36892), saving model to '/juicefs/megatron_gpt--val_loss=10.37-step=15-consumed_samples=2880.0.ckpt' as top 3
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:27:23 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:27:23 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:28:17 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:28:17 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:29:58 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:29:58 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:30:48 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:30:48 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 20/300000 [22:21<5589:38:21, v_num=0, reduced_train_loss=10.30, global_step=19.00, consumed_samples=3840.0, train_step_timing in s=4.810, val_loss=10.10]Epoch 0, global step 20: 'val_loss' reached 10.12600 (best 10.12600), saving model to '/juicefs/megatron_gpt--val_loss=10.13-step=20-consumed_samples=3840.0.ckpt' as top 3 [01:12<00:01, 21.68it/s]
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:34:11 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:34:11 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:35:06 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:35:06 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:37:04 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:37:04 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:37:59 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5969) [NeMo I 2024-08-02 00:37:59 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 25/300000 [29:59<5998:51:45, v_num=0, reduced_train_loss=9.830, global_step=24.00, consumed_samples=4800.0, train_step_timing in s=4.560, val_loss=9.740]Epoch 0, global step 25: 'val_loss' reached 9.73628 (best 9.73628), saving model to '/juicefs/megatron_gpt--val_loss=9.74-step=25-consumed_samples=4800.0.ckpt' as top 3    [01:12<00:01, 21.68it/s]
```

Run multi node with also works. Consistency works for index files
`sky launch -c a100nemo --num-nodes 2 nemo_yamls/nemo_gpt_rclone.yaml`

```
(head, rank=0, pid=5752) Epoch 0: :   0%|          | 189/300000 [3:30:45<5572:11:33, v_num=4, reduced_train_loss=6.540, global_step=188.0, consumed_samples=36288.0, train_step_timiEpoch 0: :   0%|          | 190/300000 [3:30:48<5544:02:27, v_num=4, reduced_train_loss=6.540, global_step=188.0, consumed_samples=36288.0, train_step_timing in s=2.720, val_loss=6Epoch 0: :   0%|          | 190/300000 [3:30:48<5544:02:27, v_num=4, reduced_train_loss=6.530, global_step=189.0, consumed_samples=36480.0, train_step_timing in s=2.720, val_loss=6
Epoch 0: :   0%|          | 190/300000 [3:31:26<5560:31:45, v_num=4, reduced_train_loss=6.530, global_step=189.0, consumed_samples=36480.0, train_step_timing in s=2.720, val_loss=6.480]Epoch 0, global step 190: 'val_loss' reached 6.48322 (best 6.48322), saving model to '/juicefs/megatron_gpt--val_loss=6.48-step=190-consumed_samples=36480.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:47:24 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:47:24 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:48:15 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:48:15 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:49:59 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:49:59 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:50:45 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:50:45 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 191/300000 [3:36:25<5662:02:46, v_num=4, reduced_train_loss=6.530, global_step=189.0, consumed_samples=36480.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 191/300000 [3:36:25<5662:02:47, v_num=4, reduced_train_loss=6.520, global_step=190.0, consumed_samples=36672.0, train_step_timiEpoch 0: :   0%|          | 192/300000 [3:36:28<5633:42:24, v_num=4, reduced_train_loss=6.520, global_step=190.0, consumed_samples=36672.0, train_step_timing in s=2.710, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 192/300000 [3:36:28<5633:42:25, v_num=4, reduced_train_loss=6.500, global_step=191.0, consumed_samples=36864.0, train_step_timiEpoch 0: :   0%|          | 193/300000 [3:36:31<5605:39:31, v_num=4, reduced_train_loss=6.500, global_step=191.0, consumed_samples=36864.0, train_step_timing in s=2.690, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 193/300000 [3:36:31<5605:39:32, v_num=4, reduced_train_loss=6.500, global_step=192.0, consumed_samples=37056.0, train_step_timiEpoch 0: :   0%|          | 194/300000 [3:36:33<5577:54:44, v_num=4, reduced_train_loss=6.500, global_step=192.0, consumed_samples=37056.0, train_step_timing in s=2.690, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 194/300000 [3:36:33<5577:54:45, v_num=4, reduced_train_loss=6.480, global_step=193.0, consumed_samples=37248.0, train_step_timiEpoch 0: :   0%|          | 195/300000 [3:36:36<5550:30:00, v_num=4, reduced_train_loss=6.480, global_step=193.0, consumed_samples=37248.0, train_step_timing in s=2.720, val_loss=6Epoch 0: :   0%|          | 195/300000 [3:36:36<5550:30:02, v_num=4, reduced_train_loss=6.540, global_step=194.0, consumed_samples=37440.0, train_step_timing in s=2.830, val_loss=6
(head, rank=0, pid=5752) Epoch 0: :   0%|          | 195/300000 [3:37:14<5566:42:05, v_num=4, reduced_train_loss=6.540, global_step=194.0, consumed_samples=37440.0, train_step_timing in s=2.830, val_loss=6.450]Epoch 0, global step 195: 'val_loss' reached 6.45265 (best 6.45265), saving model to '/juicefs/megatron_gpt--val_loss=6.45-step=195-consumed_samples=37440.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:53:13 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:53:13 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:54:02 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:54:02 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:55:43 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:55:43 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:56:31 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:56:31 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 196/300000 [3:42:12<5664:48:04, v_num=4, reduced_train_loss=6.540, global_step=194.0, consumed_samples=37440.0, train_step_timing in s=2.830, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 196/300000 [3:42:12<5664:48:05, v_num=4, reduced_train_loss=6.500, global_step=195.0, consumed_samples=37632.0, train_step_timiEpoch 0: :   0%|          | 197/300000 [3:42:15<5637:10:47, v_num=4, reduced_train_loss=6.500, global_step=195.0, consumed_samples=37632.0, train_step_timing in s=2.740, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 197/300000 [3:42:15<5637:10:48, v_num=4, reduced_train_loss=6.470, global_step=196.0, consumed_samples=37824.0, train_step_timiEpoch 0: :   0%|          | 198/300000 [3:42:17<5609:48:53, v_num=4, reduced_train_loss=6.470, global_step=196.0, consumed_samples=37824.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 198/300000 [3:42:17<5609:48:54, v_num=4, reduced_train_loss=6.490, global_step=197.0, consumed_samples=3.8e+4, train_step_timinEpoch 0: :   0%|          | 199/300000 [3:42:20<5582:47:50, v_num=4, reduced_train_loss=6.490, global_step=197.0, consumed_samples=3.8e+4, train_step_timing in s=2.670, val_loss=6.(head, rank=0, pid=5752) Epoch 0: :   0%|          | 199/300000 [3:42:20<5582:47:51, v_num=4, reduced_train_loss=6.490, global_step=198.0, consumed_samples=38208.0, train_step_timiEpoch 0: :   0%|          | 200/300000 [3:42:23<5555:59:55, v_num=4, reduced_train_loss=6.490, global_step=198.0, consumed_samples=38208.0, train_step_timing in s=2.840, val_loss=6Epoch 0: :   0%|          | 200/300000 [3:42:23<5555:59:55, v_num=4, reduced_train_loss=6.500, global_step=199.0, consumed_samples=38400.0, train_step_timing in s=2.720, val_loss=6
Epoch 0: :   0%|          | 200/300000 [3:43:01<5571:46:00, v_num=4, reduced_train_loss=6.500, global_step=199.0, consumed_samples=38400.0, train_step_timing in s=2.720, val_loss=6.420]Epoch 0, global step 200: 'val_loss' reached 6.41966 (best 6.41966), saving model to '/juicefs/megatron_gpt--val_loss=6.42-step=200-consumed_samples=38400.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:58:59 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:58:59 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:59:50 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 05:59:50 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:01:31 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:01:31 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:02:16 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:02:16 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 201/300000 [3:47:59<5667:34:07, v_num=4, reduced_train_loss=6.500, global_step=199.0, consumed_samples=38400.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 201/300000 [3:47:59<5667:34:09, v_num=4, reduced_train_loss=6.450, global_step=200.0, consumed_samples=38592.0, train_step_timiEpoch 0: :   0%|          | 202/300000 [3:48:02<5640:38:03, v_num=4, reduced_train_loss=6.450, global_step=200.0, consumed_samples=38592.0, train_step_timing in s=2.820, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 202/300000 [3:48:02<5640:38:04, v_num=4, reduced_train_loss=6.480, global_step=201.0, consumed_samples=38784.0, train_step_timiEpoch 0: :   0%|          | 203/300000 [3:48:04<5613:56:50, v_num=4, reduced_train_loss=6.480, global_step=201.0, consumed_samples=38784.0, train_step_timing in s=2.760, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 203/300000 [3:48:04<5613:56:50, v_num=4, reduced_train_loss=6.470, global_step=202.0, consumed_samples=3.9e+4, train_step_timinEpoch 0: :   0%|          | 204/300000 [3:48:07<5587:33:32, v_num=4, reduced_train_loss=6.470, global_step=202.0, consumed_samples=3.9e+4, train_step_timing in s=2.720, val_loss=6.(head, rank=0, pid=5752) Epoch 0: :   0%|          | 204/300000 [3:48:07<5587:33:32, v_num=4, reduced_train_loss=6.440, global_step=203.0, consumed_samples=39168.0, train_step_timiEpoch 0: :   0%|          | 205/300000 [3:48:10<5561:30:04, v_num=4, reduced_train_loss=6.440, global_step=203.0, consumed_samples=39168.0, train_step_timing in s=2.810, val_loss=6Epoch 0: :   0%|          | 205/300000 [3:48:10<5561:30:05, v_num=4, reduced_train_loss=6.450, global_step=204.0, consumed_samples=39360.0, train_step_timing in s=2.990, val_loss=6
Epoch 0: :   0%|          | 205/300000 [3:48:48<5576:50:34, v_num=4, reduced_train_loss=6.450, global_step=204.0, consumed_samples=39360.0, train_step_timing in s=2.990, val_loss=6.390]Epoch 0, global step 205: 'val_loss' reached 6.38888 (best 6.38888), saving model to '/juicefs/megatron_gpt--val_loss=6.39-step=205-consumed_samples=39360.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:04:47 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:04:47 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:05:38 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:05:38 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:07:20 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:07:20 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:08:06 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:08:06 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 206/300000 [3:53:52<5672:40:18, v_num=4, reduced_train_loss=6.450, global_step=204.0, consumed_samples=39360.0, train_step_timing in s=2.990, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 206/300000 [3:53:52<5672:40:19, v_num=4, reduced_train_loss=6.480, global_step=205.0, consumed_samples=39552.0, train_step_timiEpoch 0: :   0%|          | 207/300000 [3:53:55<5646:20:41, v_num=4, reduced_train_loss=6.480, global_step=205.0, consumed_samples=39552.0, train_step_timing in s=2.750, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 207/300000 [3:53:55<5646:20:42, v_num=4, reduced_train_loss=6.430, global_step=206.0, consumed_samples=39744.0, train_step_timiEpoch 0: :   0%|          | 208/300000 [3:53:57<5620:16:12, v_num=4, reduced_train_loss=6.430, global_step=206.0, consumed_samples=39744.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 208/300000 [3:53:57<5620:16:12, v_num=4, reduced_train_loss=6.460, global_step=207.0, consumed_samples=39936.0, train_step_timiEpoch 0: :   0%|          | 209/300000 [3:54:00<5594:26:50, v_num=4, reduced_train_loss=6.460, global_step=207.0, consumed_samples=39936.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 209/300000 [3:54:00<5594:26:51, v_num=4, reduced_train_loss=6.470, global_step=208.0, consumed_samples=40128.0, train_step_timiEpoch 0: :   0%|          | 210/300000 [3:54:03<5568:52:04, v_num=4, reduced_train_loss=6.470, global_step=208.0, consumed_samples=40128.0, train_step_timing in s=2.730, val_loss=6Epoch 0: :   0%|          | 210/300000 [3:54:03<5568:52:05, v_num=4, reduced_train_loss=6.450, global_step=209.0, consumed_samples=40320.0, train_step_timing in s=2.720, val_loss=6
Epoch 0: :   0%|          | 210/300000 [3:54:41<5583:53:06, v_num=4, reduced_train_loss=6.450, global_step=209.0, consumed_samples=40320.0, train_step_timing in s=2.720, val_loss=6.380]Epoch 0, global step 210: 'val_loss' reached 6.37592 (best 6.37592), saving model to '/juicefs/megatron_gpt--val_loss=6.38-step=210-consumed_samples=40320.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:10:39 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:10:39 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:11:31 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:11:31 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:13:11 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:13:11 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:13:57 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:13:57 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 211/300000 [3:59:36<5673:58:56, v_num=4, reduced_train_loss=6.450, global_step=209.0, consumed_samples=40320.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 211/300000 [3:59:36<5673:58:57, v_num=4, reduced_train_loss=6.420, global_step=210.0, consumed_samples=40512.0, train_step_timiEpoch 0: :   0%|          | 212/300000 [3:59:39<5648:16:15, v_num=4, reduced_train_loss=6.420, global_step=210.0, consumed_samples=40512.0, train_step_timing in s=2.740, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 212/300000 [3:59:39<5648:16:16, v_num=4, reduced_train_loss=6.360, global_step=211.0, consumed_samples=40704.0, train_step_timiEpoch 0: :   0%|          | 213/300000 [3:59:42<5622:47:54, v_num=4, reduced_train_loss=6.360, global_step=211.0, consumed_samples=40704.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 213/300000 [3:59:42<5622:47:55, v_num=4, reduced_train_loss=6.400, global_step=212.0, consumed_samples=40896.0, train_step_timiEpoch 0: :   0%|          | 214/300000 [3:59:44<5597:33:50, v_num=4, reduced_train_loss=6.400, global_step=212.0, consumed_samples=40896.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 214/300000 [3:59:44<5597:33:51, v_num=4, reduced_train_loss=6.460, global_step=213.0, consumed_samples=41088.0, train_step_timiEpoch 0: :   0%|          | 215/300000 [3:59:47<5572:34:03, v_num=4, reduced_train_loss=6.460, global_step=213.0, consumed_samples=41088.0, train_step_timing in s=2.720, val_loss=6Epoch 0: :   0%|          | 215/300000 [3:59:47<5572:34:04, v_num=4, reduced_train_loss=6.400, global_step=214.0, consumed_samples=41280.0, train_step_timing in s=2.730, val_loss=6
Epoch 0: :   0%|          | 215/300000 [4:00:25<5587:09:30, v_num=4, reduced_train_loss=6.400, global_step=214.0, consumed_samples=41280.0, train_step_timing in s=2.730, val_loss=6.350]Epoch 0, global step 215: 'val_loss' reached 6.35293 (best 6.35293), saving model to '/juicefs/megatron_gpt--val_loss=6.35-step=215-consumed_samples=41280.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:16:23 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:16:23 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:17:13 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:17:13 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:18:55 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:18:55 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:19:41 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:19:41 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 216/300000 [4:05:28<5678:15:36, v_num=4, reduced_train_loss=6.400, global_step=214.0, consumed_samples=41280.0, train_step_timing in s=2.730, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 216/300000 [4:05:28<5678:15:37, v_num=4, reduced_train_loss=6.410, global_step=215.0, consumed_samples=41472.0, train_step_timiEpoch 0: :   0%|          | 217/300000 [4:05:31<5653:08:47, v_num=4, reduced_train_loss=6.410, global_step=215.0, consumed_samples=41472.0, train_step_timing in s=2.710, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 217/300000 [4:05:31<5653:08:48, v_num=4, reduced_train_loss=6.390, global_step=216.0, consumed_samples=41664.0, train_step_timiEpoch 0: :   0%|          | 218/300000 [4:05:34<5628:13:25, v_num=4, reduced_train_loss=6.390, global_step=216.0, consumed_samples=41664.0, train_step_timing in s=2.790, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 218/300000 [4:05:34<5628:13:26, v_num=4, reduced_train_loss=6.390, global_step=217.0, consumed_samples=41856.0, train_step_timiEpoch 0: :   0%|          | 219/300000 [4:05:36<5603:31:41, v_num=4, reduced_train_loss=6.390, global_step=217.0, consumed_samples=41856.0, train_step_timing in s=2.690, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 219/300000 [4:05:36<5603:31:41, v_num=4, reduced_train_loss=6.420, global_step=218.0, consumed_samples=4.2e+4, train_step_timinEpoch 0: :   0%|          | 220/300000 [4:05:39<5579:05:24, v_num=4, reduced_train_loss=6.420, global_step=218.0, consumed_samples=4.2e+4, train_step_timing in s=2.690, val_loss=6.Epoch 0: :   0%|          | 220/300000 [4:05:39<5579:05:25, v_num=4, reduced_train_loss=6.380, global_step=219.0, consumed_samples=42240.0, train_step_timing in s=2.770, val_loss=6
(head, rank=0, pid=5752) Epoch 0: :   0%|          | 220/300000 [4:06:17<5593:19:25, v_num=4, reduced_train_loss=6.380, global_step=219.0, consumed_samples=42240.0, train_step_timing in s=2.770, val_loss=6.320]Epoch 0, global step 220: 'val_loss' reached 6.32188 (best 6.32188), saving model to '/juicefs/megatron_gpt--val_loss=6.32-step=220-consumed_samples=42240.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:22:15 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:22:15 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:23:02 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:23:02 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:24:38 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:24:38 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:25:25 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:25:25 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 221/300000 [4:11:11<5678:45:51, v_num=4, reduced_train_loss=6.380, global_step=219.0, consumed_samples=42240.0, train_step_timing in s=2.770, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 221/300000 [4:11:11<5678:45:52, v_num=4, reduced_train_loss=6.350, global_step=220.0, consumed_samples=42432.0, train_step_timiEpoch 0: :   0%|          | 222/300000 [4:11:13<5654:10:40, v_num=4, reduced_train_loss=6.350, global_step=220.0, consumed_samples=42432.0, train_step_timing in s=2.790, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 222/300000 [4:11:13<5654:10:41, v_num=4, reduced_train_loss=6.340, global_step=221.0, consumed_samples=42624.0, train_step_timiEpoch 0: :   0%|          | 223/300000 [4:11:16<5629:48:23, v_num=4, reduced_train_loss=6.340, global_step=221.0, consumed_samples=42624.0, train_step_timing in s=2.700, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 223/300000 [4:11:16<5629:48:24, v_num=4, reduced_train_loss=6.360, global_step=222.0, consumed_samples=42816.0, train_step_timiEpoch 0: :   0%|          | 224/300000 [4:11:19<5605:39:55, v_num=4, reduced_train_loss=6.360, global_step=222.0, consumed_samples=42816.0, train_step_timing in s=2.680, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 224/300000 [4:11:19<5605:39:57, v_num=4, reduced_train_loss=6.370, global_step=223.0, consumed_samples=4.3e+4, train_step_timinEpoch 0: :   0%|          | 225/300000 [4:11:22<5581:47:29, v_num=4, reduced_train_loss=6.370, global_step=223.0, consumed_samples=4.3e+4, train_step_timing in s=2.720, val_loss=6.Epoch 0: :   0%|          | 225/300000 [4:11:22<5581:47:29, v_num=4, reduced_train_loss=6.360, global_step=224.0, consumed_samples=43200.0, train_step_timing in s=2.850, val_loss=6
Epoch 0: :   0%|          | 225/300000 [4:11:59<5595:45:49, v_num=4, reduced_train_loss=6.360, global_step=224.0, consumed_samples=43200.0, train_step_timing in s=2.850, val_loss=6.300]Epoch 0, global step 225: 'val_loss' reached 6.29674 (best 6.29674), saving model to '/juicefs/megatron_gpt--val_loss=6.30-step=225-consumed_samples=43200.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:27:58 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:27:58 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:28:49 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:28:49 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:30:30 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:30:30 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:31:16 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:31:16 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 226/300000 [4:17:01<5682:05:25, v_num=4, reduced_train_loss=6.360, global_step=224.0, consumed_samples=43200.0, train_step_timing in s=2.850, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 226/300000 [4:17:01<5682:05:26, v_num=4, reduced_train_loss=6.340, global_step=225.0, consumed_samples=43392.0, train_step_timiEpoch 0: :   0%|          | 227/300000 [4:17:04<5658:01:45, v_num=4, reduced_train_loss=6.340, global_step=225.0, consumed_samples=43392.0, train_step_timing in s=2.840, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 227/300000 [4:17:04<5658:01:46, v_num=4, reduced_train_loss=6.340, global_step=226.0, consumed_samples=43584.0, train_step_timiEpoch 0: :   0%|          | 228/300000 [4:17:06<5634:11:25, v_num=4, reduced_train_loss=6.340, global_step=226.0, consumed_samples=43584.0, train_step_timing in s=2.690, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 228/300000 [4:17:06<5634:11:25, v_num=4, reduced_train_loss=6.340, global_step=227.0, consumed_samples=43776.0, train_step_timiEpoch 0: :   0%|          | 229/300000 [4:17:09<5610:33:35, v_num=4, reduced_train_loss=6.340, global_step=227.0, consumed_samples=43776.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 229/300000 [4:17:09<5610:33:36, v_num=4, reduced_train_loss=6.340, global_step=228.0, consumed_samples=4.4e+4, train_step_timinEpoch 0: :   0%|          | 230/300000 [4:17:12<5587:08:07, v_num=4, reduced_train_loss=6.340, global_step=228.0, consumed_samples=4.4e+4, train_step_timing in s=2.720, val_loss=6.Epoch 0: :   0%|          | 230/300000 [4:17:12<5587:08:07, v_num=4, reduced_train_loss=6.320, global_step=229.0, consumed_samples=44160.0, train_step_timing in s=2.720, val_loss=6
Epoch 0: :   0%|          | 230/300000 [4:17:50<5600:49:47, v_num=4, reduced_train_loss=6.320, global_step=229.0, consumed_samples=44160.0, train_step_timing in s=2.720, val_loss=6.270]Epoch 0, global step 230: 'val_loss' reached 6.27468 (best 6.27468), saving model to '/juicefs/megatron_gpt--val_loss=6.27-step=230-consumed_samples=44160.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:33:48 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:33:48 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:34:38 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:34:38 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:36:24 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:36:24 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:37:09 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:37:09 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 231/300000 [4:22:55<5686:35:38, v_num=4, reduced_train_loss=6.320, global_step=229.0, consumed_samples=44160.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 231/300000 [4:22:55<5686:35:39, v_num=4, reduced_train_loss=6.320, global_step=230.0, consumed_samples=44352.0, train_step_timiEpoch 0: :   0%|          | 232/300000 [4:22:58<5663:02:26, v_num=4, reduced_train_loss=6.320, global_step=230.0, consumed_samples=44352.0, train_step_timing in s=2.840, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 232/300000 [4:22:58<5663:02:26, v_num=4, reduced_train_loss=6.300, global_step=231.0, consumed_samples=44544.0, train_step_timiEpoch 0: :   0%|          | 233/300000 [4:23:00<5639:41:20, v_num=4, reduced_train_loss=6.300, global_step=231.0, consumed_samples=44544.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 233/300000 [4:23:00<5639:41:21, v_num=4, reduced_train_loss=6.330, global_step=232.0, consumed_samples=44736.0, train_step_timiEpoch 0: :   0%|          | 234/300000 [4:23:03<5616:32:17, v_num=4, reduced_train_loss=6.330, global_step=232.0, consumed_samples=44736.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 234/300000 [4:23:03<5616:32:17, v_num=4, reduced_train_loss=6.320, global_step=233.0, consumed_samples=44928.0, train_step_timiEpoch 0: :   0%|          | 235/300000 [4:23:06<5593:35:04, v_num=4, reduced_train_loss=6.320, global_step=233.0, consumed_samples=44928.0, train_step_timing in s=2.720, val_loss=6Epoch 0: :   0%|          | 235/300000 [4:23:06<5593:35:05, v_num=4, reduced_train_loss=6.310, global_step=234.0, consumed_samples=45120.0, train_step_timing in s=2.720, val_loss=6
Epoch 0: :   0%|          | 235/300000 [4:23:43<5606:54:46, v_num=4, reduced_train_loss=6.310, global_step=234.0, consumed_samples=45120.0, train_step_timing in s=2.720, val_loss=6.240]Epoch 0, global step 235: 'val_loss' reached 6.24493 (best 6.24493), saving model to '/juicefs/megatron_gpt--val_loss=6.24-step=235-consumed_samples=45120.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:39:42 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:39:42 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:40:33 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:40:33 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:42:14 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:42:14 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:43:00 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:43:00 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 236/300000 [4:28:44<5689:03:51, v_num=4, reduced_train_loss=6.310, global_step=234.0, consumed_samples=45120.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 236/300000 [4:28:44<5689:03:52, v_num=4, reduced_train_loss=6.320, global_step=235.0, consumed_samples=45312.0, train_step_timiEpoch 0: :   0%|          | 237/300000 [4:28:46<5665:59:33, v_num=4, reduced_train_loss=6.320, global_step=235.0, consumed_samples=45312.0, train_step_timing in s=2.750, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 237/300000 [4:28:46<5665:59:33, v_num=4, reduced_train_loss=6.260, global_step=236.0, consumed_samples=45504.0, train_step_timiEpoch 0: :   0%|          | 238/300000 [4:28:49<5643:06:41, v_num=4, reduced_train_loss=6.260, global_step=236.0, consumed_samples=45504.0, train_step_timing in s=2.700, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 238/300000 [4:28:49<5643:06:41, v_num=4, reduced_train_loss=6.300, global_step=237.0, consumed_samples=45696.0, train_step_timiEpoch 0: :   0%|          | 239/300000 [4:28:52<5620:25:19, v_num=4, reduced_train_loss=6.300, global_step=237.0, consumed_samples=45696.0, train_step_timing in s=2.700, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 239/300000 [4:28:52<5620:25:20, v_num=4, reduced_train_loss=6.290, global_step=238.0, consumed_samples=45888.0, train_step_timiEpoch 0: :   0%|          | 240/300000 [4:28:54<5597:55:25, v_num=4, reduced_train_loss=6.290, global_step=238.0, consumed_samples=45888.0, train_step_timing in s=2.700, val_loss=6Epoch 0: :   0%|          | 240/300000 [4:28:54<5597:55:26, v_num=4, reduced_train_loss=6.290, global_step=239.0, consumed_samples=46080.0, train_step_timing in s=2.700, val_loss=6
(head, rank=0, pid=5752) Epoch 0: :   0%|          | 240/300000 [4:29:33<5611:09:53, v_num=4, reduced_train_loss=6.290, global_step=239.0, consumed_samples=46080.0, train_step_timing in s=2.700, val_loss=6.230]Epoch 0, global step 240: 'val_loss' reached 6.22856 (best 6.22856), saving model to '/juicefs/megatron_gpt--val_loss=6.23-step=240-consumed_samples=46080.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:45:31 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:45:31 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:46:21 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:46:21 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:48:04 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:48:04 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:48:50 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:48:50 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 241/300000 [4:34:35<5692:23:19, v_num=4, reduced_train_loss=6.290, global_step=239.0, consumed_samples=46080.0, train_step_timing in s=2.700, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 241/300000 [4:34:35<5692:23:20, v_num=4, reduced_train_loss=6.270, global_step=240.0, consumed_samples=46272.0, train_step_timiEpoch 0: :   0%|          | 242/300000 [4:34:38<5669:48:42, v_num=4, reduced_train_loss=6.270, global_step=240.0, consumed_samples=46272.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 242/300000 [4:34:38<5669:48:43, v_num=4, reduced_train_loss=6.280, global_step=241.0, consumed_samples=46464.0, train_step_timiEpoch 0: :   0%|          | 243/300000 [4:34:41<5647:23:04, v_num=4, reduced_train_loss=6.280, global_step=241.0, consumed_samples=46464.0, train_step_timing in s=2.800, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 243/300000 [4:34:41<5647:23:05, v_num=4, reduced_train_loss=6.270, global_step=242.0, consumed_samples=46656.0, train_step_timiEpoch 0: :   0%|          | 244/300000 [4:34:43<5625:08:56, v_num=4, reduced_train_loss=6.270, global_step=242.0, consumed_samples=46656.0, train_step_timing in s=2.690, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 244/300000 [4:34:43<5625:08:56, v_num=4, reduced_train_loss=6.220, global_step=243.0, consumed_samples=46848.0, train_step_timiEpoch 0: :   0%|          | 245/300000 [4:34:46<5603:05:03, v_num=4, reduced_train_loss=6.220, global_step=243.0, consumed_samples=46848.0, train_step_timing in s=2.720, val_loss=6Epoch 0: :   0%|          | 245/300000 [4:34:46<5603:05:04, v_num=4, reduced_train_loss=6.210, global_step=244.0, consumed_samples=4.7e+4, train_step_timing in s=2.690, val_loss=6.
Epoch 0: :   0%|          | 245/300000 [4:35:24<5615:56:14, v_num=4, reduced_train_loss=6.210, global_step=244.0, consumed_samples=4.7e+4, train_step_timing in s=2.690, val_loss=6.210]Epoch 0, global step 245: 'val_loss' reached 6.21360 (best 6.21360), saving model to '/juicefs/megatron_gpt--val_loss=6.21-step=245-consumed_samples=47040.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:51:23 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:51:23 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:52:14 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:52:14 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:53:58 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:53:58 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:54:45 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:54:45 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 246/300000 [4:40:35<5698:22:16, v_num=4, reduced_train_loss=6.210, global_step=244.0, consumed_samples=4.7e+4, train_step_timing in s=2.690, val_loss=6.(head, rank=0, pid=5752) Epoch 0: :   0%|          | 246/300000 [4:40:35<5698:22:17, v_num=4, reduced_train_loss=6.220, global_step=245.0, consumed_samples=47232.0, train_step_timiEpoch 0: :   0%|          | 247/300000 [4:40:38<5676:10:57, v_num=4, reduced_train_loss=6.220, global_step=245.0, consumed_samples=47232.0, train_step_timing in s=2.690, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 247/300000 [4:40:38<5676:10:58, v_num=4, reduced_train_loss=6.240, global_step=246.0, consumed_samples=47424.0, train_step_timiEpoch 0: :   0%|          | 248/300000 [4:40:40<5654:10:29, v_num=4, reduced_train_loss=6.240, global_step=246.0, consumed_samples=47424.0, train_step_timing in s=2.670, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 248/300000 [4:40:40<5654:10:29, v_num=4, reduced_train_loss=6.260, global_step=247.0, consumed_samples=47616.0, train_step_timiEpoch 0: :   0%|          | 249/300000 [4:40:43<5632:20:13, v_num=4, reduced_train_loss=6.260, global_step=247.0, consumed_samples=47616.0, train_step_timing in s=2.670, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 249/300000 [4:40:43<5632:20:14, v_num=4, reduced_train_loss=6.270, global_step=248.0, consumed_samples=47808.0, train_step_timiEpoch 0: :   0%|          | 250/300000 [4:40:46<5610:40:50, v_num=4, reduced_train_loss=6.270, global_step=248.0, consumed_samples=47808.0, train_step_timing in s=2.650, val_loss=6Epoch 0: :   0%|          | 250/300000 [4:40:46<5610:40:50, v_num=4, reduced_train_loss=6.260, global_step=249.0, consumed_samples=4.8e+4, train_step_timing in s=2.670, val_loss=6.
(head, rank=0, pid=5752) Epoch 0: :   0%|          | 250/300000 [4:41:23<5623:14:03, v_num=4, reduced_train_loss=6.260, global_step=249.0, consumed_samples=4.8e+4, train_step_timing in s=2.670, val_loss=6.190]Epoch 0, global step 250: 'val_loss' reached 6.18812 (best 6.18812), saving model to '/juicefs/megatron_gpt--val_loss=6.19-step=250-consumed_samples=48000.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:57:22 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:57:22 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:58:11 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:58:11 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:59:53 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 06:59:53 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:00:41 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:00:41 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 251/300000 [4:46:28<5702:00:49, v_num=4, reduced_train_loss=6.260, global_step=249.0, consumed_samples=4.8e+4, train_step_timing in s=2.670, val_loss=6.(head, rank=0, pid=5752) Epoch 0: :   0%|          | 251/300000 [4:46:28<5702:00:50, v_num=4, reduced_train_loss=6.210, global_step=250.0, consumed_samples=48192.0, train_step_timiEpoch 0: :   0%|          | 252/300000 [4:46:31<5680:17:12, v_num=4, reduced_train_loss=6.210, global_step=250.0, consumed_samples=48192.0, train_step_timing in s=2.970, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 252/300000 [4:46:31<5680:17:13, v_num=4, reduced_train_loss=6.240, global_step=251.0, consumed_samples=48384.0, train_step_timiEpoch 0: :   0%|          | 253/300000 [4:46:34<5658:42:14, v_num=4, reduced_train_loss=6.240, global_step=251.0, consumed_samples=48384.0, train_step_timing in s=2.780, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 253/300000 [4:46:34<5658:42:15, v_num=4, reduced_train_loss=6.220, global_step=252.0, consumed_samples=48576.0, train_step_timiEpoch 0: :   0%|          | 254/300000 [4:46:37<5637:17:26, v_num=4, reduced_train_loss=6.220, global_step=252.0, consumed_samples=48576.0, train_step_timing in s=2.690, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 254/300000 [4:46:37<5637:17:26, v_num=4, reduced_train_loss=6.230, global_step=253.0, consumed_samples=48768.0, train_step_timiEpoch 0: :   0%|          | 255/300000 [4:46:39<5616:03:07, v_num=4, reduced_train_loss=6.230, global_step=253.0, consumed_samples=48768.0, train_step_timing in s=2.690, val_loss=6Epoch 0: :   0%|          | 255/300000 [4:46:39<5616:03:08, v_num=4, reduced_train_loss=6.200, global_step=254.0, consumed_samples=4.9e+4, train_step_timing in s=2.710, val_loss=6.
Epoch 0: :   0%|          | 255/300000 [4:47:17<5628:20:26, v_num=4, reduced_train_loss=6.200, global_step=254.0, consumed_samples=4.9e+4, train_step_timing in s=2.710, val_loss=6.160]Epoch 0, global step 255: 'val_loss' reached 6.15772 (best 6.15772), saving model to '/juicefs/megatron_gpt--val_loss=6.16-step=255-consumed_samples=48960.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:03:16 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:03:16 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:04:06 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:04:06 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:05:47 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:05:47 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:06:34 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:06:34 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 256/300000 [4:52:20<5704:56:59, v_num=4, reduced_train_loss=6.200, global_step=254.0, consumed_samples=4.9e+4, train_step_timing in s=2.710, val_loss=6.(head, rank=0, pid=5752) Epoch 0: :   0%|          | 256/300000 [4:52:20<5704:57:00, v_num=4, reduced_train_loss=6.220, global_step=255.0, consumed_samples=49152.0, train_step_timiEpoch 0: :   0%|          | 257/300000 [4:52:23<5683:35:46, v_num=4, reduced_train_loss=6.220, global_step=255.0, consumed_samples=49152.0, train_step_timing in s=2.690, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 257/300000 [4:52:23<5683:35:46, v_num=4, reduced_train_loss=6.200, global_step=256.0, consumed_samples=49344.0, train_step_timiEpoch 0: :   0%|          | 258/300000 [4:52:25<5662:24:43, v_num=4, reduced_train_loss=6.200, global_step=256.0, consumed_samples=49344.0, train_step_timing in s=2.660, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 258/300000 [4:52:25<5662:24:44, v_num=4, reduced_train_loss=6.250, global_step=257.0, consumed_samples=49536.0, train_step_timiEpoch 0: :   0%|          | 259/300000 [4:52:28<5641:23:18, v_num=4, reduced_train_loss=6.250, global_step=257.0, consumed_samples=49536.0, train_step_timing in s=2.670, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 259/300000 [4:52:28<5641:23:19, v_num=4, reduced_train_loss=6.220, global_step=258.0, consumed_samples=49728.0, train_step_timiEpoch 0: :   0%|          | 260/300000 [4:52:31<5620:31:46, v_num=4, reduced_train_loss=6.220, global_step=258.0, consumed_samples=49728.0, train_step_timing in s=2.660, val_loss=6Epoch 0: :   0%|          | 260/300000 [4:52:31<5620:31:47, v_num=4, reduced_train_loss=6.220, global_step=259.0, consumed_samples=49920.0, train_step_timing in s=2.670, val_loss=6
Epoch 0: :   0%|          | 260/300000 [4:53:08<5632:36:40, v_num=4, reduced_train_loss=6.220, global_step=259.0, consumed_samples=49920.0, train_step_timing in s=2.670, val_loss=6.140]Epoch 0, global step 260: 'val_loss' reached 6.14080 (best 6.14080), saving model to '/juicefs/megatron_gpt--val_loss=6.14-step=260-consumed_samples=49920.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:09:07 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:09:07 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:09:57 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:09:57 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:11:41 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:11:41 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:12:28 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:12:28 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 261/300000 [4:58:16<5709:00:01, v_num=4, reduced_train_loss=6.220, global_step=259.0, consumed_samples=49920.0, train_step_timing in s=2.670, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 261/300000 [4:58:16<5709:00:02, v_num=4, reduced_train_loss=6.150, global_step=260.0, consumed_samples=50112.0, train_step_timiEpoch 0: :   0%|          | 262/300000 [4:58:18<5688:02:35, v_num=4, reduced_train_loss=6.150, global_step=260.0, consumed_samples=50112.0, train_step_timing in s=2.720, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 262/300000 [4:58:18<5688:02:35, v_num=4, reduced_train_loss=6.210, global_step=261.0, consumed_samples=50304.0, train_step_timiEpoch 0: :   0%|          | 263/300000 [4:58:21<5667:14:48, v_num=4, reduced_train_loss=6.210, global_step=261.0, consumed_samples=50304.0, train_step_timing in s=2.680, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 263/300000 [4:58:21<5667:14:48, v_num=4, reduced_train_loss=6.190, global_step=262.0, consumed_samples=50496.0, train_step_timiEpoch 0: :   0%|          | 264/300000 [4:58:24<5646:36:11, v_num=4, reduced_train_loss=6.190, global_step=262.0, consumed_samples=50496.0, train_step_timing in s=2.680, val_loss=6(head, rank=0, pid=5752) Epoch 0: :   0%|          | 264/300000 [4:58:24<5646:36:11, v_num=4, reduced_train_loss=6.190, global_step=263.0, consumed_samples=50688.0, train_step_timiEpoch 0: :   0%|          | 265/300000 [4:58:26<5626:09:11, v_num=4, reduced_train_loss=6.190, global_step=263.0, consumed_samples=50688.0, train_step_timing in s=2.670, val_loss=6Epoch 0: :   0%|          | 265/300000 [4:58:26<5626:09:14, v_num=4, reduced_train_loss=6.220, global_step=264.0, consumed_samples=50880.0, train_step_timing in s=2.790, val_loss=6
Epoch 0: :   0%|          | 265/300000 [4:59:05<5638:09:36, v_num=4, reduced_train_loss=6.220, global_step=264.0, consumed_samples=50880.0, train_step_timing in s=2.790, val_loss=6.140]Epoch 0, global step 265: 'val_loss' reached 6.13796 (best 6.13796), saving model to '/juicefs/megatron_gpt--val_loss=6.14-step=265-consumed_samples=50880.0.ckpt' as top 3
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:15:03 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:15:03 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:15:54 nemo_model_checkpoint:226] New .nemo model saved to: /juicefs/megatron_gpt.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:15:54 nemo_model_checkpoint:228] Removing old .nemo backup /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:17:37 nemo_model_checkpoint:299] /juicefs/megatron_gpt.nemo already exists, moving existing checkpoint to /juicefs/megatron_gpt-v1.nemo
(head, rank=0, pid=5752) [NeMo I 2024-08-01 07:17:37 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
```

### Real world - VSCode SkyPilot Tutorial

```
(base) root@a100nemo-2ea4-head-6fr3jme2-compute:/rclone_mount# time git clone https://github.com/skypilot-org/skypilot-tutorial.git
```
```
(base) root@a100nemo-2ea4-head-dy5g78ob-compute:/juicefs# time git clone https://github.com/skypilot-org/skypilot-tutorial.git
Cloning into 'skypilot-tutorial'...
remote: Enumerating objects: 186, done.
remote: Counting objects: 100% (186/186), done.
remote: Compressing objects: 100% (121/121), done.
remote: Total 186 (delta 80), reused 161 (delta 59), pack-reused 0
Receiving objects: 100% (186/186), 62.28 KiB | 115.00 KiB/s, done.
Resolving deltas: 100% (80/80), done.
Updating files: 100% (15/15), done.

real    0m11.926s
user    0m0.079s
sys     0m0.086s
```

Notebooks work.

### Real world - Tensorboard logs

Tensorboard works fine. Can also be accessed remotely - but only on the first load. Subsequent reloads fail. 
```
[2024-08-01T01:53:05Z WARN  rustboard_core::run] Read error in /juicefs/tblogs/20240801-014652/validation/events.out.tfevents.1722476820.a100nemo-2ea4-head-dy5g78ob-compute.32289.1.v2: ReadRecordError(BadLengthCrc(ChecksumError { got: MaskedCrc(0x07980329), want: MaskedCrc(0x00000000) }))
```

### JuiceFS Benchmark

+------------------+-------------------+----------------+
|       ITEM       |       VALUE       |      COST      |
+------------------+-------------------+----------------+
|   Write big file |      349.16 MiB/s |   35.19 s/file |
|    Read big file |      266.43 MiB/s |   46.12 s/file |
| Write small file |      34.3 files/s | 349.55 ms/file |
|  Read small file |     586.6 files/s |  20.46 ms/file |
|        Stat file |    1733.9 files/s |   6.92 ms/file |
|   FUSE operation | 215523 operations |     7.11 ms/op |
|      Update meta |  14865 operations |    10.04 ms/op |
|       Put object |   4272 operations |   175.03 ms/op |
|       Get object |   3072 operations |   633.24 ms/op |
|    Delete object |      0 operations |     0.00 ms/op |
| Write into cache |   4214 operations |     3.21 ms/op |
|  Read from cache |   1200 operations |     0.08 ms/op |
+------------------+-------------------+----------------+






## Google Filestore NFS

### Synthetic benchmark

```
(storage-demo, pid=7062) ===== Benchmark Results =====
(storage-demo, pid=7062) All results are reported as (bandwidth, IOPS)
(storage-demo, pid=7062) 
(storage-demo, pid=7062) ##### Sequential Read Results #####
(storage-demo, pid=7062)        566.17 MB/s     539.94 IOPS
(storage-demo, pid=7062) 
(storage-demo, pid=7062) ##### Sequential Write Results #####
(storage-demo, pid=7062)        99.54 MB/s      94.93 IOPS
(storage-demo, pid=7062) 
(storage-demo, pid=7062) ##### Small Files Read Results #####
(storage-demo, pid=7062)        476.63 MB/s     454.55 IOPS
(storage-demo, pid=7062) 
(storage-demo, pid=7062) ##### Small Files Write Results #####
(storage-demo, pid=7062)        519.10 MB/s     495.05 IOPS
INFO: Job finished (status: SUCCEEDED).
I 08-01 14:50:55 cloud_vm_ray_backend.py:3297] Job ID: 1
```

===== CROSS REGION ======
VM in us-central1, filestore in me-west1. Works, but awful perf.

```
(storage-demo, pid=4687) ===== Benchmark Results =====
(storage-demo, pid=4687) All results are reported as (bandwidth, IOPS)
(storage-demo, pid=4687) 
(storage-demo, pid=4687) ##### Sequential Read Results #####
(storage-demo, pid=4687)        20.51 MB/s      19.56 IOPS
(storage-demo, pid=4687) 
(storage-demo, pid=4687) ##### Sequential Write Results #####
(storage-demo, pid=4687)        19.21 MB/s      18.32 IOPS
(storage-demo, pid=4687) 
(storage-demo, pid=4687) ##### Small Files Read Results #####
(storage-demo, pid=4687)        18.76 MB/s      17.89 IOPS
(storage-demo, pid=4687) 
(storage-demo, pid=4687) ##### Small Files Write Results #####
(storage-demo, pid=4687)        6.28 MB/s       5.99 IOPS
```

### Real world - NeMo Read + Write (Checkpoint)

Filestore wont mount on ubuntu 22.04 (Nemo image). Works fine on Ubuntu 24.04 (SkyPilot image).

`mount.nfs: Protocol not supported`

Fixed by changing to NFS 4.1.
`sky exec a100nemo --env DATASET_ROOT=/nfs --env CHECKPOINT_PATH=/nfs --num-nodes 1 nemo_yamls/nemo_gpt_rclone.yaml
`
```
Epoch 0: :   0%|          | 5/300000 [01:41<1699:14:58, v_num=0, reduced_train_loss=11.00, global_step=4.000, consumed_samples=960.0, train_step_timing in s=4.560, val_loss=11.00]Epoch 0, global step 5: 'val_loss' reached 10.95736 (best 10.95736), saving model to '/nfs/megatron_gpt--val_loss=10.96-step=5-consumed_samples=960.0.ckpt' as top 3█████▊| 1568/1600 [01:12<00:01, 21.66it/s]
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:50:41 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:51:02 nemo_model_checkpoint:226] New .nemo model saved to: /nfs/megatron_gpt.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:51:20 nemo_model_checkpoint:299] /nfs/megatron_gpt.nemo already exists, moving existing checkpoint to /nfs/megatron_gpt-v1.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:51:21 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:51:41 nemo_model_checkpoint:226] New .nemo model saved to: /nfs/megatron_gpt.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:51:41 nemo_model_checkpoint:228] Removing old .nemo backup /nfs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 10/300000 [04:37<2314:10:03, v_num=0, reduced_train_loss=11.00, global_step=9.000, consumed_samples=1920.0, train_step_timing in s=4.580, val_loss=11.00]Epoch 0, global step 10: 'val_loss' reached 10.95736 (best 10.95736), saving model to '/nfs/megatron_gpt--val_loss=10.96-step=10-consumed_samples=1920.0.ckpt' as top 3▊| 1568/1600 [01:12<00:01, 21.71it/s]
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:53:37 nemo_model_checkpoint:299] /nfs/megatron_gpt.nemo already exists, moving existing checkpoint to /nfs/megatron_gpt-v1.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:53:37 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:53:59 nemo_model_checkpoint:226] New .nemo model saved to: /nfs/megatron_gpt.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:53:59 nemo_model_checkpoint:228] Removing old .nemo backup /nfs/megatron_gpt-v1.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:54:25 nemo_model_checkpoint:299] /nfs/megatron_gpt.nemo already exists, moving existing checkpoint to /nfs/megatron_gpt-v1.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:54:25 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:54:46 nemo_model_checkpoint:226] New .nemo model saved to: /nfs/megatron_gpt.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:54:46 nemo_model_checkpoint:228] Removing old .nemo backup /nfs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 15/300000 [07:52<2625:07:24, v_num=0, reduced_train_loss=10.60, global_step=14.00, consumed_samples=2880.0, train_step_timing in s=4.570, val_loss=10.40]Epoch 0, global step 15: 'val_loss' reached 10.36892 (best 10.36892), saving model to '/nfs/megatron_gpt--val_loss=10.37-step=15-consumed_samples=2880.0.ckpt' as top 3▊| 1568/1600 [01:12<00:01, 21.70it/s]
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:56:52 nemo_model_checkpoint:299] /nfs/megatron_gpt.nemo already exists, moving existing checkpoint to /nfs/megatron_gpt-v1.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:56:52 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:57:15 nemo_model_checkpoint:226] New .nemo model saved to: /nfs/megatron_gpt.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:57:15 nemo_model_checkpoint:228] Removing old .nemo backup /nfs/megatron_gpt-v1.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:57:43 nemo_model_checkpoint:299] /nfs/megatron_gpt.nemo already exists, moving existing checkpoint to /nfs/megatron_gpt-v1.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:57:43 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:58:04 nemo_model_checkpoint:226] New .nemo model saved to: /nfs/megatron_gpt.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 20:58:04 nemo_model_checkpoint:228] Removing old .nemo backup /nfs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 20/300000 [11:11<2796:09:37, v_num=0, reduced_train_loss=10.30, global_step=19.00, consumed_samples=3840.0, train_step_timing in s=4.570, val_loss=10.10]Epoch 0, global step 20: 'val_loss' reached 10.12600 (best 10.12600), saving model to '/nfs/megatron_gpt--val_loss=10.13-step=20-consumed_samples=3840.0.ckpt' as top 3▊| 1568/1600 [01:12<00:01, 21.71it/s]
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 21:00:10 nemo_model_checkpoint:299] /nfs/megatron_gpt.nemo already exists, moving existing checkpoint to /nfs/megatron_gpt-v1.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 21:00:10 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 21:00:32 nemo_model_checkpoint:226] New .nemo model saved to: /nfs/megatron_gpt.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 21:00:32 nemo_model_checkpoint:228] Removing old .nemo backup /nfs/megatron_gpt-v1.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 21:01:00 nemo_model_checkpoint:299] /nfs/megatron_gpt.nemo already exists, moving existing checkpoint to /nfs/megatron_gpt-v1.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 21:01:00 dist_ckpt_io:95] Using ('zarr', 1) dist-ckpt save strategy.
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 21:01:22 nemo_model_checkpoint:226] New .nemo model saved to: /nfs/megatron_gpt.nemo
(worker1, rank=0, pid=6023, ip=10.178.0.63) [NeMo I 2024-08-02 21:01:22 nemo_model_checkpoint:228] Removing old .nemo backup /nfs/megatron_gpt-v1.nemo
Epoch 0: :   0%|          | 25/300000 [13:17<2657:00:06, v_num=0, reduced_train_loss=9.830, global_step=24.00, consumed_samples=4800.0, train_step_timing in s=4.570, val_loss=10.10]rain_step_timing in s=4.570, val_loss=10.10]
```



### Real world - VSCode SkyPilot Tutorial
Git clone of the SkyPilot tutorial repo took 1.92s.
```console
(base) gcpuser@storagetest2-2ea4-head-6a3nkdsh-compute:/nfs$ time git clone https://github.com/skypilot-org/skypilot-tutorial.git
Cloning into 'skypilot-tutorial'...
remote: Enumerating objects: 186, done.
remote: Counting objects: 100% (186/186), done.
remote: Compressing objects: 100% (121/121), done.
remote: Total 186 (delta 80), reused 161 (delta 59), pack-reused 0
Receiving objects: 100% (186/186), 62.28 KiB | 436.00 KiB/s, done.
Resolving deltas: 100% (80/80), done.

real    0m1.920s
user    0m0.044s
sys     0m0.032s
```



## [Unused] Kubernetes cluster setup

We use GKE for benchmarking on Kubernetes.

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
