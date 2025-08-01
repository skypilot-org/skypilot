# TPU v6e

Trillium (also refers to v6e) is Cloud TPU’s latest generation AI accelerator. SkyPilot support TPU v6e with provisioning, training and serving.

## Catalogs

Currently, for TPU v6e, the public APIs for regions and pricing is not released yet, and pricing info for `us-central1`, `us-central2`, `us-south1` is not available. We set the price to `0.0` in those regions for now.

```
## Provisioning

To provision TPU v6e, use the following command:

```bash
$ sky launch --gpus tpu-v6e-16 -c tpu-v6e
```

After that, you can SSH to the instance and start developing your model:

```bash
$ ssh tpu-v6e
```

## Training

Examples in this directory (`train-llama3-8b.yaml`) shows how to use TPU v6e to train a Llama3 8b model, using PyTorch (XLA) on the wikitext dataset. To start the training, use the following command:

```bash
$ HF_TOKEN=hf_xxx sky launch train-llama3-8b.yaml -c train-llama3-8b --secret HF_TOKEN
```

### Single-Host Training

The training throughput for a `tpu-v6e-8` instance should around 0.5 samples/s:

```bash
(task, pid=17499) ***** train metrics *****
(task, pid=17499)   epoch                    =      1.1765
(task, pid=17499)   total_flos               = 109935420GF
(task, pid=17499)   train_loss               =     10.6011
(task, pid=17499)   train_runtime            =  0:11:12.77
(task, pid=17499)   train_samples            =         282
(task, pid=17499)   train_samples_per_second =       0.476
(task, pid=17499)   train_steps_per_second   =        0.03
INFO: Job finished (status: SUCCEEDED).
```

### Multi-Host Training

By changing the TPU type to `tpu-v6e-16` and the `--per_device_train_batch_size` to `32`, the training throughput increased to around 1 samples/s:

```bash
(head, rank=0, pid=17894) ***** train metrics *****
(head, rank=0, pid=17894)   epoch                    =         2.5
(head, rank=0, pid=17894)   total_flos               = 219870840GF
(head, rank=0, pid=17894)   train_loss               =     10.1527
(head, rank=0, pid=17894)   train_runtime            =  0:11:13.18
(head, rank=0, pid=17894)   train_samples            =         282
(head, rank=0, pid=17894)   train_samples_per_second =       0.951
(head, rank=0, pid=17894)   train_steps_per_second   =        0.03

(worker1, rank=1, pid=15406, ip=10.164.0.57) ***** train metrics *****
(worker1, rank=1, pid=15406, ip=10.164.0.57)   epoch                    =         2.5
(worker1, rank=1, pid=15406, ip=10.164.0.57)   total_flos               = 219870840GF
(worker1, rank=1, pid=15406, ip=10.164.0.57)   train_loss               =     10.1527
(worker1, rank=1, pid=15406, ip=10.164.0.57)   train_runtime            =  0:11:15.08
(worker1, rank=1, pid=15406, ip=10.164.0.57)   train_samples            =         282
(worker1, rank=1, pid=15406, ip=10.164.0.57)   train_samples_per_second =       0.948
(worker1, rank=1, pid=15406, ip=10.164.0.57)   train_steps_per_second   =        0.03

(worker2, rank=2, pid=16552, ip=10.164.0.58) ***** train metrics *****
(worker2, rank=2, pid=16552, ip=10.164.0.58)   epoch                    =         2.5
(worker2, rank=2, pid=16552, ip=10.164.0.58)   total_flos               = 219870840GF
(worker2, rank=2, pid=16552, ip=10.164.0.58)   train_loss               =     10.1527
(worker2, rank=2, pid=16552, ip=10.164.0.58)   train_runtime            =  0:11:15.61
(worker2, rank=2, pid=16552, ip=10.164.0.58)   train_samples            =         282
(worker2, rank=2, pid=16552, ip=10.164.0.58)   train_samples_per_second =       0.947
(worker2, rank=2, pid=16552, ip=10.164.0.58)   train_steps_per_second   =        0.03

(worker3, rank=3, pid=17469, ip=10.164.0.59) ***** train metrics *****
(worker3, rank=3, pid=17469, ip=10.164.0.59)   epoch                    =         2.5
(worker3, rank=3, pid=17469, ip=10.164.0.59)   total_flos               = 219870840GF
(worker3, rank=3, pid=17469, ip=10.164.0.59)   train_loss               =     10.1527
(worker3, rank=3, pid=17469, ip=10.164.0.59)   train_runtime            =  0:11:15.10
(worker3, rank=3, pid=17469, ip=10.164.0.59)   train_samples            =         282
(worker3, rank=3, pid=17469, ip=10.164.0.59)   train_samples_per_second =       0.948
(worker3, rank=3, pid=17469, ip=10.164.0.59)   train_steps_per_second   =        0.03

INFO: Job finished (status: SUCCEEDED).
```

# Serving

TPU v6e also supports serving. Examples in this directory (`serve-llama2-7b.yaml`) shows how to use TPU v6e to serve a Llama2 7b model, using PyTorch (XLA) and the JetStream lib. To start the serving, use the following command:

```bash
$ HF_TOKEN=hf_xxx sky launch serve-llama2-7b.yaml -c serve-llama2-7b --secret HF_TOKEN
```

After the server is ready, you should see the following message:

```bash
(task, pid=26431) 2024-09-24 19:58:15,160 - root - INFO - Starting server on port 9000 with 64 threads
(task, pid=26431) I0924 19:58:15.160293 140454572087296 server_lib.py:155] Starting server on port 9000 with 64 threads
(task, pid=26431) 2024-09-24 19:58:15,161 - root - INFO - Not starting JAX profiler server: False
(task, pid=26431) I0924 19:58:15.161907 140454572087296 server_lib.py:164] Not starting JAX profiler server: False
(task, pid=26431) Started jetstream_server....
```

You can now start a benchmark to test the serving performance:

```bash
$ sky exec serve-llama2-7b benchmark-llama2-7b.yaml
... (emitted logs)
(task, pid=25491) Successful requests: 100
(task, pid=25491) Benchmark duration: 8.753792 s
(task, pid=25491) Total input tokens: 21888
(task, pid=25491) Total generated tokens: 18803
(task, pid=25491) Request throughput: 11.42 requests/s
(task, pid=25491) Input token throughput: 2500.40 tokens/s
(task, pid=25491) Output token throughput: 2147.98 tokens/s
(task, pid=25491) Mean TTFT: 1981.93 ms
(task, pid=25491) Median TTFT: 1829.33 ms
(task, pid=25491) P99 TTFT: 4511.95 ms
(task, pid=25491) Mean TPOT: 130.71 ms
(task, pid=25491) Median TPOT: 18.88 ms
(task, pid=25491) P99 TPOT: 2487.37 ms
```
