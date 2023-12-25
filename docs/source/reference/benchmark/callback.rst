.. _benchmark-skycallback:

SkyCallback
===========

SkyCallback is a simple Python library that works in conjunction with SkyPilot Benchmark.
It enables SkyPilot to provide a more detailed benchmark report without the need to wait until the task finishes.

What SkyCallback is for
--------------------------------------------

SkyCallback is designed for **machine learning tasks** which have a loop iterating many `steps`.
SkyCallback measures the average time taken by each step, and extrapolates it to the total execution time of the task.

Installing SkyCallback
--------------------------------------------

Unlike SkyPilot, SkyCallback must be installed and imported `in your program`.
To install it, add the following line in the ``setup`` section of your task YAML.

.. code-block:: yaml

    setup:
        # Activate conda or virtualenv if you use one
        # Then, install SkyCallback
        pip install "git+https://github.com/skypilot-org/skypilot.git#egg=sky-callback&subdirectory=sky/callbacks/"


Using SkyCallback generic APIs
--------------------------------------------

The SkyCallback generic APIs are for **PyTorch, TensorFlow, and JAX** programs where training loops are exposed to the users.
Below we provide the instructions for using the APIs.

First, import the SkyCallback package and initialize it using ``init``.

.. code-block:: python

    import sky_callback
    sky_callback.init()

Next, mark the beginning and end of each step using one of the three equivalent methods.

.. code-block:: python

    # Method 1: wrap your iterable (e.g., dataloader) with `step_iterator`.
    from sky_callback import step_iterator
    for batch in step_iterator(train_dataloader):
        ...

    # Method 2: wrap your loop body with the `step` context manager.
    for batch in train_dataloader:
        with sky_callback.step():
            ...

    # Method 3: call `step_begin` and `step_end` directly.
    for batch in train_dataloader:
        sky_callback.step_begin()
        ...
        sky_callback.step_end()

That's it.
Now you can launch your task and get a detailed benchmark report using SkyPilot Benchmark CLI.
`Here <https://github.com/skypilot-org/skypilot/blob/master/examples/benchmark/timm/callback.patch>`__ we provide an example of applying SkyCallback to Pytorch ImageNet training.

.. note::
    
    Optionally in ``sky_callback.init``, you can specify the total number of steps that the task will iterate through.
    This information is needed to estimate the total execution time/cost of your task.

    .. code-block:: python
    
        sky_callback.init(
            total_steps=num_epochs * len(train_dataloader), # Optional
        )

.. note::
    In distributed training, ``global_rank`` should be additionally passed to ``sky_callback.init`` as follows:

    .. code-block:: python

        # PyTorch DDP users
        global_rank = torch.distributed.get_rank()

        # Horovod users
        global_rank = hvd.rank()

        sky_callback.init(
            global_rank=global_rank,
            total_steps=num_epochs * len(train_dataloader), # Optional
        )

Integrations with ML frameworks
----------------------------------------------------------

Using SkyCallback is even easier for **Keras, PytorchLightning, and HuggingFace Transformers** programs where trainer APIs are used.
SkyCallback natively supports these frameworks with simple interface.

* Keras example

.. code-block:: python

    from sky_callback import SkyKerasCallback

    # Add the callback to your Keras model.
    model.fit(..., callbacks=[SkyKerasCallback()])

`Here <https://github.com/skypilot-org/skypilot/blob/master/examples/benchmark/keras_asr/callback.patch>`__ you can find an example of applying SkyCallback to Keras ASR model training.

* PytorchLightning example

.. code-block:: python

    from sky_callback import SkyLightningCallback

    # Add the callback to your trainer.
    trainer = pl.Trainer(..., callbacks=[SkyLightningCallback()])

`Here <https://github.com/skypilot-org/skypilot/blob/master/examples/benchmark/lightning_gan/callback.patch>`__ you can find an example of applying SkyCallback to PyTorchLightning GAN model training.

* HuggingFace Transformers example

.. code-block:: python

    from sky_callback import SkyTransformersCallback

    # Add the callback to your trainer.
    trainer = transformers.Trainer(..., callbacks=[SkyTransformersCallback()])

`Here <https://github.com/skypilot-org/skypilot/blob/master/examples/benchmark/transformers_qa/callback.patch>`__ you can find an example of applying SkyCallback to HuggingFace BERT fine-tuning.

.. note::
    When using the framework-integrated callbacks, do not call ``sky_callback.init`` for initialization.
    The callbacks will do it for you.
