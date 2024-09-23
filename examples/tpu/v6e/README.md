# TPU v6e

Trillium (also refers to v6e) is Cloud TPU’s latest generation AI accelerator. SkyPilot support TPU v6e with provisioning, training and inferencing.

## Catalogs

Currently, for TPU v6e, the public APIs for regions and pricing is not released yet. The current enabled availability zones for TPU v6e includes `us-south1-a`, `europe-west4-a` and `us-east5-b`. To use TPU v6e, add the following at the end of `~/.sky/catalogs/v5/gcp/vms.csv`:

```csv
,,,tpu-v6e-1,1,tpu-v6e-1,us-south1,us-south1-a,0,0
,,,tpu-v6e-1,1,tpu-v6e-1,europe-west4,europe-west4-a,0,0
,,,tpu-v6e-1,1,tpu-v6e-1,us-east5,us-east5-b,0,0
,,,tpu-v6e-4,1,tpu-v6e-4,us-south1,us-south1-a,0,0
,,,tpu-v6e-4,1,tpu-v6e-4,europe-west4,europe-west4-a,0,0
,,,tpu-v6e-4,1,tpu-v6e-4,us-east5,us-east5-b,0,0
,,,tpu-v6e-8,1,tpu-v6e-8,us-south1,us-south1-a,0,0
,,,tpu-v6e-8,1,tpu-v6e-8,europe-west4,europe-west4-a,0,0
,,,tpu-v6e-8,1,tpu-v6e-8,us-east5,us-east5-b,0,0
,,,tpu-v6e-16,1,tpu-v6e-16,us-south1,us-south1-a,0,0
,,,tpu-v6e-16,1,tpu-v6e-16,europe-west4,europe-west4-a,0,0
,,,tpu-v6e-16,1,tpu-v6e-16,us-east5,us-east5-b,0,0
,,,tpu-v6e-32,1,tpu-v6e-32,us-south1,us-south1-a,0,0
,,,tpu-v6e-32,1,tpu-v6e-32,europe-west4,europe-west4-a,0,0
,,,tpu-v6e-32,1,tpu-v6e-32,us-east5,us-east5-b,0,0
,,,tpu-v6e-64,1,tpu-v6e-64,us-south1,us-south1-a,0,0
,,,tpu-v6e-64,1,tpu-v6e-64,europe-west4,europe-west4-a,0,0
,,,tpu-v6e-64,1,tpu-v6e-64,us-east5,us-east5-b,0,0
,,,tpu-v6e-128,1,tpu-v6e-128,us-south1,us-south1-a,0,0
,,,tpu-v6e-128,1,tpu-v6e-128,europe-west4,europe-west4-a,0,0
,,,tpu-v6e-128,1,tpu-v6e-128,us-east5,us-east5-b,0,0
,,,tpu-v6e-256,1,tpu-v6e-256,us-south1,us-south1-a,0,0
,,,tpu-v6e-256,1,tpu-v6e-256,europe-west4,europe-west4-a,0,0
,,,tpu-v6e-256,1,tpu-v6e-256,us-east5,us-east5-b,0,0
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
cd examples/tpu/v6e
HF_TOKEN=hf_xxx sky launch train-llama3-8b.yaml -c train-llama3-8b --env HF_TOKEN
```

The training should finished in ~10 minutes for a `tpu-v6e-8` instance:

```bash
(task, pid=17499) ***** train metrics *****
(task, pid=17499)   epoch                    =      1.1765
(task, pid=17499)   total_flos               = 109935420GF
(task, pid=17499)   train_loss               =     10.6011
(task, pid=17499)   train_runtime            =  0:11:12.77
(task, pid=17499)   train_samples            =         282
(task, pid=17499)   train_samples_per_second =       0.476
(task, pid=17499)   train_steps_per_second   =        0.03
(task, pid=17499) [INFO|modelcard.py:450] 2024-09-23 17:49:49,776 >> Dropping the following result as it does not have all the necessary fields:
(task, pid=17499) {'task': {'name': 'Causal Language Modeling', 'type': 'text-generation'}, 'dataset': {'name': 'wikitext wikitext-2-raw-v1', 'type': 'wikitext', 'args': 'wikitext-2-raw-v1'}}
INFO: Job finished (status: SUCCEEDED).
```
