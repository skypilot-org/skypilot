# Example Sky apps

To launch an example:
```python
python examples/<name>.py
```

Machine learning examples:

1. [**`resnet_app.py`**](./resnet_app.py): ResNet50 training on GPUs, adapted from [tensorflow/tpu](https://github.com/tensorflow/tpu). 
  
    The training data is currently a public, "fake_imagenet" dataset (`gs://cloud-tpu-test-datasets/fake_imagenet`, 70GB).
    
2. [**`resnet_distributed_tf_app.py`**](./resnet_distributed_tf_app.py): **Distributed training** variant of the above, via TensorFlow Distributed.

3. [**`resnet_distributed_torch_app.py`**](./resnet_distributed_torch_app.py): Distributed training variant of the above, via PyTorch Distributed.

4. [**`huggingface_glue_imdb_app.py`**](./huggingface_glue_imdb_app.py): Use [Huggingface Transformers](https://github.com/huggingface/transformers/) to finetune a pretrained BERT model.
 
5. [**`huggingface_glue_imdb_grid_search_app.py`**](./huggingface_glue_imdb_grid_search_app.py): Run **grid search** on the above.  Run many trials concurrently on the same VM.

6. [**`tpu_app.py`**](./tpu_app.py): **Train on a TPU** (v3-8) on GCP.  Finetune BERT on Amazon Reviews for sentiment analysis.


General examples:

1. [**`multi_echo.py`**](./multi_echo.py): Launch and schedule hundreds of bash commands in the cloud, with configurable resources.  Similar to grid search.

## TODO: non-runnable apps
1. [**`timm_app.py`**](./timm_app.py): the [PyTorch image models (timm)](https://github.com/rwightman/pytorch-image-models) package.
  - Not runnable due to requiring ImageNet images, not tfrecords; consider using https://github.com/mlcommons/inference/blob/master/vision/classification_and_detection/tools/make_fake_imagenet.sh.
