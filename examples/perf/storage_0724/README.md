# Benchmarking Storage options for K8s clusters

This directory is a collection of YAMLs used to benchmark SkyPilot's performance.

## Running on raw VMs

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

Use the 



#### JuiceFS

#### Google Filestore 

### Running the benchmarks

#### Synthetic benchmarks
Run `sky launch -c a100 --env BENCH_PATH=/skystorage-mount synthetic_benchmarks.yaml` to run the synthetic benchmarks.

#### Real world - NeMo Read + Write (Checkpoint)
See yamls in nemo_yamls directory. Run them and observe the batch time and checkpointing time. VAL_CHECK_INTERVAL determines how frequently the model is saved.

Takes about 30 min to start.

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
