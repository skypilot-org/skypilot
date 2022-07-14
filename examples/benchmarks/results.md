# Sky Benchmarks

This directory is a collection of YAMLs used to benchmark Sky's performance. 

## Sky Storage Benchmarks
We benchmark the performance of Sky Storage using fio, a popular storage
benchmarking utility. For fio, we use recommended parameters from [Azure's 
disk benchmarking guide](https://docs.microsoft.com/en-us/azure/virtual-machines/disks-benchmarks).
Please refer to `storage_rawperf.yaml` for more details.
All benchmarks on run on AWS m5.8xlarge with default EBS volume attached with 
4 parallel processes reading/writing to files.

### Disk Bandwidth (MB/s)

|                  | EBS | Sky Storage<br/>(S3, MOUNT mode)     |
|------------------|-----|--------------------------------------|
| Sequential Read  | 121 | 358                                  |
| Sequential Write | 128 | 1800                                 |


### Disk IOPS

|                  | EBS    | Sky Storage<br/>(S3, MOUNT mode) |
|------------------|--------|----------------------------------|
| Sequential Read  | 5740   | 1944                             |
| Sequential Write | 28807  | 1800                             |

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
* Sky Storage offers higher **read** performance than EBS because it can directly
  stream files from S3 to memory.
* Sky Storage **write** performance is significantly higher than EBS because it
  writes to memory and then asynchronously uploads to S3. Sky Storage offers 
  only eventual consistency, so a write operation to sky storage may not reflect 
  immediately on the S3 storage.
