# Performance benchmarks for SkyPilot

This directory is a collection of YAMLs used to benchmark SkyPilot's performance. 

## SkyPilot Storage Benchmarks
We benchmark the performance of SkyPilot Storage using fio, a popular storage
benchmarking utility. For fio, we use recommended parameters from [Azure's 
disk benchmarking guide](https://docs.microsoft.com/en-us/azure/virtual-machines/disks-benchmarks).
Please refer to `storage_rawperf.yaml` for more details.
All benchmarks on run on AWS m5.8xlarge with default EBS volume attached with 
4 parallel processes reading/writing to files.

### Disk Bandwidth (MB/s)

|                  | EBS | SkyPilot Storage<br/>(S3, MOUNT mode) |
|------------------|-----|----------------------------------|
| Sequential Read  | 130 | 642                              |
| Sequential Write | 129 | 1828                             |


### Disk IOPS

|                  | EBS  | SkyPilot Storage<br/>(S3, MOUNT mode) |
|------------------|------|----------------------------------|
| Sequential Read  | 2051 | 8462                             |
| Sequential Write | 2055 | 27899                            |

### S3 -> EBS copy bandwidth (MB/s)

We also run `aws s3 cp s3://<bucket> <local_path>` to measure S3->EBS bandwidth.
We copy a 1 GB file from S3 to EBS using `aws s3 cp` command.

|           | `aws s3 cp` bandwidth |
|-----------|-----------------------|
| S3 -> EBS | 92 MB/s               |

### Discussion
* These benchmarks use the default EBS gp3 volume with the standard 125MB/s 
  throughput and 16000 IOPS. It is possible to achieve higher performance by 
  paying more.
* SkyPilot Storage offers higher **read** performance than EBS because it can directly
  stream files from S3 to memory.
* SkyPilot Storage **write** performance is significantly higher than EBS because it
  writes directly to S3 over network instead of disk.
* SkyPilot Storage offers close-to-open consistency, so it is guaranteed that when a file is closed, 
  subsequent opens will see the latest changes. 
* These benchmarks are run on single large files. The performance of SkyPilot Storage
  degrades when using many small files.
