.. _batch-processing:

Sky Batch
=========

.. tip::

  Use Sky Batch to process large datasets across a pool of GPU/CPU workers with a simple Python API. No infrastructure management required.

Sky Batch lets you distribute data processing across cloud GPUs and on-prem clusters at scale. Given a dataset and a processing function, Sky Batch splits the data into batches, distributes them across a pool of workers, and collects the results, all in a few lines of Python.

.. contents:: Contents
   :local:
   :backlinks: none


Why Sky Batch?
--------------

Processing large datasets on GPUs and compute clusters (running inference on millions of prompts, generating images from text, computing embeddings) typically requires significant infrastructure work: provisioning machines, distributing data, handling failures, and collecting results.

Sky Batch handles all of this for you:

#. **Simple Python API**: Define your processing logic as a Python function. Sky Batch distributes it across workers automatically.
#. **Automatic data distribution**: Your dataset is split into batches and assigned to workers. No manual sharding or coordination needed.
#. **Reuse runtime environments**: Workers are reused across jobs, so expensive setup (installing packages, downloading model weights, loading models onto GPUs) only happens once.
#. **Multi-cloud storage**: Read inputs from and write outputs to Amazon S3 (``s3://``) or Google Cloud Storage (``gs://``).


How it works
------------

Sky Batch has three steps:

1. **Create a Dataset**: Point to your input data in cloud storage.
2. **Start a worker pool**: Define the hardware and software each worker needs.
3. **Map a function over the dataset**: Write your processing logic and let Sky Batch distribute it.

.. code-block:: text

  ┌──────────────────────┐
  │ Your Python script   │
  │                      │
  │  ds = Dataset(...)   │      ┌────────────────┐
  │  ds.map(my_fn, ...)  │─────▶│  Worker Pool   │
  │                      │      │  ┌──────────┐  │
  └──────────────────────┘      │  │ Worker 0 │  │  ──▶  Results in
                                │  │ Worker 1 │  │       cloud storage
         Input data             │  │ Worker 2 │  │
       (cloud storage)  ───────▶│  │   ...    │  │
                                │  └──────────┘  │
                                └────────────────┘


Quickstart
----------

This example takes a JSONL file of text strings and doubles each one, writing the results back to cloud storage.

Step 1: Prepare your input data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a JSONL file (one JSON object per line) and upload it to cloud storage:

.. code-block:: console

  $ cat > /tmp/data.jsonl << 'EOF'
  {"text": "hello"}
  {"text": "world"}
  {"text": "sky"}
  {"text": "batch"}
  EOF

  $ aws s3 cp /tmp/data.jsonl s3://my-bucket/data.jsonl

Step 2: Create a worker pool
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a YAML file that describes what hardware and software each worker needs:

.. code-block:: yaml

  # pool.yaml
  pool:
    workers: 2

  resources:
    cpus: 1+

  setup: echo "Setup complete!"

Start the pool:

.. code-block:: console

  $ sky jobs pool apply pool.yaml --pool my-pool -y

Step 3: Process the dataset
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Write a Python script that defines your processing function and runs it over the dataset:

.. code-block:: python

  import sky

  # 1. Point to your input data
  ds = sky.batch.Dataset(sky.batch.JsonReader("s3://my-bucket/data.jsonl"))

  # 2. Define your processing function
  @sky.batch.remote_function
  def double_text():
      for batch in sky.batch.load():
          results = [{"output": item["text"] * 2} for item in batch]
          sky.batch.save_results(results)

  # 3. Run it
  ds.map(
      double_text,
      pool_name="my-pool",
      batch_size=2,
      output=sky.batch.JsonWriter("s3://my-bucket/output.jsonl"),
  )

Run the script:

.. code-block:: console

  $ python my_script.py

A progress bar will appear showing batch completion. When finished, your results are in cloud storage:

.. code-block:: console

  $ aws s3 cp s3://my-bucket/output.jsonl /tmp/output.jsonl
  $ cat /tmp/output.jsonl
  {"output": "hellohello"}
  {"output": "worldworld"}
  {"output": "skysky"}
  {"output": "batchbatch"}


.. _batch-processing-dataset:

Creating a Dataset
------------------

A Dataset wraps an input reader that tells Sky Batch where your data lives and how to read it. The built-in :code:`JsonReader` reads JSONL files from cloud storage:

.. code-block:: python

  ds = sky.batch.Dataset(sky.batch.JsonReader("s3://my-bucket/prompts.jsonl"))

The JSONL file should have one JSON object per line, all following the same schema:

.. code-block:: json

  {"prompt": "Summarize the theory of relativity"}
  {"prompt": "Write a haiku about mountains"}

Supported cloud storage: Amazon S3 (``s3://``) and Google Cloud Storage (``gs://``).


.. _batch-processing-pool:

Configuring the worker pool
---------------------------

The worker pool defines what hardware and software each worker has. Create a YAML file:

.. code-block:: yaml

  # pool.yaml
  pool:
    workers: 3          # Number of parallel workers

  resources:
    accelerators: L4:1  # GPU type and count per worker

  setup: |
    pip install vllm    # Install dependencies

.. list-table::
   :header-rows: 1

   * - Field
     - Description
     - Examples
   * - :code:`pool.workers`
     - Number of parallel workers
     - ``1``, ``10``, ``100``
   * - :code:`resources.accelerators`
     - GPU type and count per worker
     - ``L4:1``, ``A100:2``, ``H100:8``
   * - :code:`setup`
     - Commands to install dependencies
     - ``pip install vllm``

Start the pool with:

.. code-block:: console

  $ sky jobs pool apply pool.yaml --pool my-pool -y

The pool stays running after your job finishes, so subsequent jobs skip the setup time entirely. See :ref:`pool` for more details on pool management, scaling, and autoscaling.


.. _batch-processing-mapper:

Writing a mapper function
-------------------------

The mapper function contains your processing logic. It runs on each worker in the pool.

.. code-block:: python

  @sky.batch.remote_function
  def my_mapper():
      # (Optional) One-time setup: load a model, open a connection, etc.
      # This runs once per worker and is reused across all batches.

      for batch in sky.batch.load():
          # batch is a list of dicts from your input, e.g.:
          # [{"prompt": "hello"}, {"prompt": "world"}]

          results = [process(item) for item in batch]

          # Save results (one dict per input item, same order)
          sky.batch.save_results(results)

The key elements:

- :code:`@sky.batch.remote_function`: Marks the function for remote execution on workers.
- :code:`sky.batch.load()`: A generator that yields batches of input data. Each batch is a list of dicts.
- :code:`sky.batch.save_results(results)`: Saves processed results. Call once per batch, with results in the same order as the input.

.. note::

  The mapper function runs on remote workers. All imports must be placed **inside** the function body, not at the top of your script:

  .. code-block:: python

    @sky.batch.remote_function
    def my_mapper():
        import torch             # Import inside the function
        from transformers import pipeline

        model = pipeline("sentiment-analysis")
        for batch in sky.batch.load():
            results = model([item["text"] for item in batch])
            sky.batch.save_results(results)


.. _batch-processing-output:

Writing results
---------------

Use :code:`sky.batch.save_results()` to save your results. Each call writes one result per input item, and the dict keys become column names (like rows in a table).

For example, if your mapper calls:

.. code-block:: python

  sky.batch.save_results([
      {"prompt": "hello", "output": "Hello! How can I help?", "score": 0.95},
      {"prompt": "world", "output": "The world is vast.",     "score": 0.87},
  ])

This writes two rows:

.. list-table::
   :header-rows: 1

   * - prompt
     - output
     - score
   * - hello
     - Hello! How can I help?
     - 0.95
   * - world
     - The world is vast.
     - 0.87

Output formats
~~~~~~~~~~~~~~~

Sky Batch provides built-in output writers:

.. list-table::
   :header-rows: 1

   * - Writer
     - Description
   * - :code:`JsonWriter(path)`
     - Writes results as a JSONL file (one JSON object per line).
   * - :code:`JsonWriter(path, column="output")`
     - Writes only the specified column(s) to JSONL.
   * - :code:`ImageWriter(path, column="image")`
     - Saves PIL Images as individual PNG files to a directory.

You can also define your own input and output formats. See :ref:`batch-custom-formats` for a full guide.

Column filtering
~~~~~~~~~~~~~~~~~

The :code:`column` parameter selects which columns to include in the output, similar to a SQL ``SELECT``:

.. code-block:: python

  # Write all columns
  sky.batch.JsonWriter("s3://bucket/full.jsonl")

  # Write only the "output" column
  sky.batch.JsonWriter("s3://bucket/outputs.jsonl", column="output")

  # Write "prompt" and "score" columns
  sky.batch.JsonWriter("s3://bucket/meta.jsonl", column=["prompt", "score"])

Multi-output
~~~~~~~~~~~~~

You can write to multiple destinations at once by passing a list of writers. Each writer independently selects the columns it needs from the same results:

.. code-block:: python

  ds.map(
      my_mapper,
      pool_name="my-pool",
      batch_size=32,
      output=[
          sky.batch.ImageWriter("s3://bucket/images/", column="image"),
          sky.batch.JsonWriter("s3://bucket/meta.jsonl", column=["prompt", "score"]),
      ],
  )


.. _batch-processing-running:

Running a batch job
-------------------

Call :code:`ds.map()` to distribute your function across the worker pool:

.. code-block:: python

  ds.map(
      my_mapper,                                            # Your processing function
      pool_name="my-pool",                                  # Name of the worker pool
      batch_size=32,                                        # Items per batch
      output=sky.batch.JsonWriter("s3://bucket/out.jsonl"), # Where to write results
  )

.. list-table::
   :header-rows: 1

   * - Parameter
     - Description
   * - :code:`mapper_fn`
     - Function decorated with :code:`@sky.batch.remote_function`.
   * - :code:`pool_name`
     - Name of the worker pool (created with :code:`sky jobs pool apply`).
   * - :code:`batch_size`
     - Number of items each worker processes at a time.
   * - :code:`output`
     - An output writer or list of output writers.
   * - :code:`activate_env`
     - (Optional) Shell command to activate an environment, e.g. ``source .venv/bin/activate``.
   * - :code:`stream`
     - (Optional) Whether to block and stream progress. Default is ``True``. Set to ``False`` to return immediately after submission.

:code:`ds.map()` blocks until all batches are processed, displaying a progress bar. Progress is also visible via :code:`sky jobs queue`.


Example: GPU image generation
------------------------------

This example generates images from text prompts using Stable Diffusion, distributed across GPU workers.

**Pool configuration** (``pool.yaml``):

.. code-block:: yaml

  pool:
    workers: 3

  resources:
    accelerators: L4:1

  setup: |
    uv venv .venv
    source .venv/bin/activate
    uv pip install torch torchvision diffusers transformers accelerate safetensors

**Processing script**:

.. code-block:: python

  import sky

  @sky.batch.remote_function
  def generate_images():
      from diffusers import StableDiffusionPipeline
      import torch

      pipe = StableDiffusionPipeline.from_pretrained(
          "stable-diffusion-v1-5/stable-diffusion-v1-5",
          torch_dtype=torch.float16,
      )
      pipe = pipe.to("cuda")

      for batch in sky.batch.load():
          prompts = [item["prompt"] for item in batch]
          result = pipe(prompts)

          results = []
          for item, img in zip(batch, result.images):
              results.append({"prompt": item["prompt"], "image": img})

          sky.batch.save_results(results)


  ds = sky.batch.Dataset(sky.batch.JsonReader("s3://my-bucket/prompts.jsonl"))

  ds.map(
      generate_images,
      pool_name="diffusion-pool",
      batch_size=3,
      output=[
          sky.batch.ImageWriter("s3://my-bucket/images/", column="image"),
          sky.batch.JsonWriter("s3://my-bucket/manifest.jsonl", column=["prompt"]),
      ],
      activate_env="source .venv/bin/activate",
  )

This will:

1. Load Stable Diffusion once on each worker (amortized across all batches).
2. Distribute prompts across 3 GPU workers in batches of 3.
3. Save generated images as PNGs and a manifest mapping prompts to filenames.

Run it:

.. code-block:: console

  $ sky jobs pool apply pool.yaml --pool diffusion-pool -y
  $ python generate_images.py

  $ aws s3 cp s3://my-bucket/images/ ./images/ --recursive


Monitoring and managing jobs
-----------------------------

Check job status:

.. code-block:: console

  $ sky jobs queue

Cancel a running batch job:

.. code-block:: console

  $ sky jobs cancel <job-id>

Cancelling a batch job stops processing but leaves the worker pool running. The pool is a shared resource and can be reused for other jobs.

Tear down the pool when done:

.. code-block:: console

  $ sky jobs pool down my-pool -y


Fault tolerance
----------------

Sky Batch automatically handles failures:

- **Worker failures**: If a worker crashes or is preempted, its in-progress batches are reassigned to other workers.
- **Automatic retries**: Failed batches are retried up to 3 times with exponential backoff.
- **Partial result recovery**: If a job fails partway through, completed batch results are preserved in cloud storage. You can recover them manually:

  .. code-block:: python

    writer = sky.batch.JsonWriter("s3://bucket/output.jsonl")
    writer.reduce_results(job_id="42")
    writer.cleanup(job_id="42")  # Remove temporary batch files

  The job ID is printed in the failure message, so you can copy-paste directly. You can omit the ``cleanup()`` call to keep the temporary files for debugging.

.. toctree::
   :hidden:
   :maxdepth: 1

   custom-formats
