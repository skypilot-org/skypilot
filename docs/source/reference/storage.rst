Storage
=======

A Sky Storage object represents an abstract data store containing large data files
required by the task. Compared to file_mounts, storage is faster and
can persist across runs, requiring fewer uploads from your local machine.
A storage object is used by "mounting" it to a task. On mounting, the data
specified in the source becomes available at the destination mount_path.
Please note that sky.Storage does not guarantee preservation of file
permissions - you may need to set file permissions during task execution. These are specified
in Sky YAML configurations with :code:`storage`.

Behind the scenes, storage automatically uploads all data in the source
to a backing object store in a particular cloud (S3/GCS/Azure Blob).

Storage mounts specify where the storage objects defined above should be
mounted when the task is run. These are specified with :code:`storage_mounts`.

In YAML form, this looks something like:

.. code-block:: yaml

   storage:
      - name: imagenet-bucket
         source: s3://imagenet-bucket
         force_stores: [s3] # Could be [s3, gcs], [gcs] default: None
         persistent: True

   storage_mounts:
      - storage: imagenet-bucket
         mount_path: /tmp/imagenet

   setup: |
      . $(conda info --base)/etc/profile.d/conda.sh
      pip install --upgrade pip
      conda activate resnet
      if [ $? -eq 0 ]; then
         echo "conda env exists"
      else
         conda create -n resnet python=3.7 -y
         conda activate resnet
         pip install tensorflow==2.4.0 pyyaml
         cd models
         pip install -e .
      fi

   run: |
      . $(conda info --base)/etc/profile.d/conda.sh
      conda activate resnet
      export XLA_FLAGS='--xla_gpu_cuda_data_dir=/usr/local/cuda/'
      python -u models/official/resnet/resnet_main.py --use_tpu=False \
            --mode=train --train_batch_size=256 --train_steps=250 \
            --iterations_per_loop=125 \
            --data_dir=/tmp/imagenet \
            --model_dir=resnet-model-dir \
            --amp --xla --loss_scale=128
