# Example Sky apps

1. [**`resnet_app.py`**](./resnet_app.py): ResNet50 training on GPUs, adapted from [tensorflow/tpu](https://github.com/tensorflow/tpu).

    Do the following before running the app:
    ```bash
    # To run on GPUs, we prepared a fork that added a few changes.
    git clone -b gpu_train https://github.com/concretevitamin/tpu ~/Downloads/tpu
    pushd ~/Downloads/tpu
    git checkout 222cc86b5
    # See diff against the last common commit with upstream: git diff adecd6
    popd
    ```
