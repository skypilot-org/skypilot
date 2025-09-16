# Training benchmarks

This directory includes two training tests to benchmark storage performance and network performance of a GPU cluster.

These were tested on `Nebius` with H100s and H200s using kubernetes.

Please edit the yamls as you like.

To run disk tests, run `sky launch e2e_disk.yaml -c e2e_disk --env HF_TOKEN="YOUR TOKEN"`

Requirements for disk benchmark, 2 s3 buckets (one for mount and one for mount cached) and 1 pvc (Check out [volumnes](https://docs.skypilot.co/en/stable/reference/volumes.html))

Expected output, something like:

```bash
[
  {
    "run_id": 1,
    "timestamp": "2025-08-05T01:22:17.221591",
    "dataset_name": "open-r1/codeforces-cots",
    "checkpoint_dir": "/checkpoints_s3",
    "model_id": "google/gemma-3-12b-it",
    "training_status": "completed",
    "output_dir": "/checkpoints_s3",
    "dataset_load_time": 2.6743061542510986,
    "model_load_time": 48.94017267227173,
    "training_time": 3200.5458493232727,
    "total_time": 3252.1603281497955,
    "error": null,
    "num_checkpoints_saved": 5,
    "total_checkpoint_save_time": 1286.7577648162842,
    "average_checkpoint_save_time": 257.35155296325684,
    "min_checkpoint_save_time": 242.82270002365112,
    "max_checkpoint_save_time": 284.6724410057068,
    "individual_checkpoint_save_times": [
      284.6724410057068,
      263.4689667224884,
      246.7314112186432,
      249.06224584579468,
      242.82270002365112
    ],
    "num_batch_samples": 25,
    "total_batch_sample_time": 147.39336824417114,
    "average_batch_sample_time": 5.895734729766846,
    "min_batch_sample_time": 0.4951050281524658,
    "max_batch_sample_time": 33.0719780921936,
    "individual_batch_sample_times": [
      0.4951050281524658,
      1.2477593421936035,
      0.9029757976531982,
      0.8013198375701904,
      1.0406615734100342,
      33.0719780921936,
      0.982867956161499,
      0.7813241481781006,
      0.7629773616790771,
      0.6265926361083984,
      32.265892028808594,
      0.8771781921386719,
      0.8207690715789795,
      0.5521945953369141,
      0.899928092956543,
      31.929020404815674,
      0.7563271522521973,
      0.8896903991699219,
      0.9805104732513428,
      1.1163063049316406,
      32.243717670440674,
      0.989912748336792,
      0.8619399070739746,
      0.5947456359863281,
      0.9016737937927246
    ],
    ...
]
```

For network benchmarking, run `sky launch e2e_network.yaml -c e2e_network --env HF_TOKEN="YOUR TOKEN"` and change `network_tier`

```sh
# For gpt-oss-120b

============================================================
TRAINING TIME STATISTICS
============================================================
Total training steps: 80
Total training time: 3170.00 seconds
Average time per step: 39.625 seconds
Fastest step: 34.978 seconds
Slowest step: 52.793 seconds
Time variance: 17.815 seconds

Step time distribution:
  Step 1: 47.909s
  Step 2: 37.763s
  Step 3: 38.361s
  Step 4: 38.000s
  Step 5: 42.169s
  Step 6: 38.862s
  Step 7: 39.023s
  Step 8: 37.826s
  Step 9: 39.864s
  Step 10: 37.722s
  Step 11: 38.529s
  Step 12: 38.192s
  Step 13: 39.092s
  Step 14: 38.131s
  Step 15: 40.026s
  Step 16: 41.520s
  Step 17: 37.178s
  Step 18: 40.633s
  Step 19: 38.732s
  Step 20: 37.072s
  Step 21: 41.382s
  Step 22: 38.205s
  Step 23: 35.518s
  Step 24: 39.715s
  Step 25: 44.308s
  Step 26: 38.584s
  Step 27: 41.809s
  Step 28: 38.013s
  Step 29: 39.132s
  Step 30: 39.982s
  Step 31: 38.289s
  Step 32: 37.872s
  Step 33: 52.793s
  Step 34: 38.423s
  Step 35: 39.729s
  Step 36: 39.188s
  Step 37: 38.741s
  Step 38: 39.523s
  Step 39: 37.451s
  Step 40: 35.268s
  Step 41: 37.604s
  Step 42: 38.516s
  Step 43: 38.687s
  Step 44: 38.814s
  Step 45: 46.564s
  Step 46: 39.022s
  Step 47: 39.392s
  Step 48: 40.496s
  Step 49: 38.937s
  Step 50: 39.640s
  Step 51: 37.715s
  Step 52: 41.568s
  Step 53: 39.754s
  Step 54: 41.888s
  Step 55: 45.673s
  Step 56: 37.169s
  Step 57: 34.978s
  Step 58: 38.400s
  Step 59: 40.962s
  Step 60: 39.532s
  Step 61: 37.050s
  Step 62: 37.420s
  Step 63: 37.540s
  Step 64: 38.513s
  Step 65: 39.181s
  Step 66: 39.669s
  Step 67: 37.358s
  Step 68: 36.917s
  Step 69: 49.274s
  Step 70: 40.236s
  Step 71: 46.094s
  Step 72: 37.478s
  Step 73: 38.832s
  Step 74: 37.429s
  Step 75: 40.205s
  Step 76: 38.209s
  Step 77: 40.905s
  Step 78: 38.281s
  Step 79: 42.660s
  Step 80: 40.901s
```

To change the model (which is defaulted to `openai/gpt-oss-120b`), pass it as a parameter when launching the script -- `sky launch e2e_network.yaml -c e2e_network --env HF_TOKEN="YOUR TOKEN" MODEL_ID="google/gemma-3-12b-it"`

