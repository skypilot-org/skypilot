# Example SkyPilot applications

To launch an example:
```bash
# Recommended: YAML + CLI
sky launch examples/<name>.yaml

# Advanced: programmatic API
python examples/<name>.py
```

Machine learning examples:
- [**`dvc_pipeline.yaml`**](./dvc/dvc_pipeline.yaml): Use [DVC](https://dvc.org) to easily run ML pipelines on the cloud and version-control the results in git and cloud buckets. An existing [DVC remote](https://dvc.org/doc/user-guide/data-management/remote-storage) and [DVC pipeline](https://dvc.org/doc/start/data-management/data-pipelines) are prerequisites. A detailed tutorial is available [here](https://alex000kim.com/posts/2023-08-10-ml-experiments-in-cloud-skypilot-dvc/).

- [**`huggingface_glue_imdb_app.yaml`**](./huggingface_glue_imdb_app.yaml): Use [Huggingface Transformers](https://github.com/huggingface/transformers/) to finetune a pretrained BERT model.

- [**`resnet_distributed_torch.yaml`**](./resnet_distributed_torch.yaml): Run Distributed PyTorch (DDP) training of ResNet50 on 2 nodes.

- [**`detectron2_app.yaml`**](./detectron2_app.yaml): Run Detectron2 on a V100 GPU.

- TPU examples
  - [**`tpu/tpu_app.yaml`**](./tpu/tpu_app.yaml): Train on a **TPU VM** on GCP.  Finetune BERT on Amazon Reviews for sentiment analysis.
  - [**`tpu/tpuvm_mnist.yaml`**](./tpu/tpuvm_mnist.yaml): Train on a **TPU VM** on GCP.  Train on MNIST in Flax (based on JAX).

- [**`resnet_app.py`**](./resnet_app.py): ResNet50 training on GPUs, adapted from [tensorflow/tpu](https://github.com/tensorflow/tpu).

    The training data is currently a public, "fake_imagenet" dataset (`gs://cloud-tpu-test-datasets/fake_imagenet`, 70GB).


- [**`resnet_distributed_tf_app.py`**](./resnet_distributed_tf_app.py): **Distributed training** variant of the above, via TensorFlow Distributed.

- [**`huggingface_glue_imdb_grid_search_app.py`**](./huggingface_glue_imdb_grid_search_app.py): **Grid search**: run many trials concurrently on the same VM.


...and many more.

General examples:

- [**`detectron2_docker.yaml`**](./detectron2_docker.yaml): Using Docker to run Detectron2 on GPUs.

- [**`using_file_mounts.yaml`**](./using_file_mounts.yaml): Using `file_mounts` to upload local/cloud paths to a cluster.

- [**`multi_hostname.yaml`**](./multi_hostname.yaml): Run a command on multiple nodes.

- [**`env_check.yaml`**](./env_check.yaml): Using environment variables in the `run` commands.

- [**`multi_echo.py`**](./multi_echo.py): Launch and schedule hundreds of bash commands on the clouds, with configurable resources.  Similar to grid search.

...and many more.
