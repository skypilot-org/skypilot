.. _batch-custom-formats:

Custom I/O Formats
==================

Sky Batch ships with built-in formats (:code:`JsonReader`, :code:`JsonWriter`, :code:`ImageWriter`), but you can define your own without modifying SkyPilot source code.

This guide walks through writing custom **InputReaders** (to read your data) and **OutputWriters** (to save your results).

.. seealso::

   :ref:`batch-processing` for an introduction to Sky Batch.


.. contents:: Contents
   :local:
   :backlinks: none


How data flows through Sky Batch
---------------------------------

Before writing custom formats, it helps to understand the data flow:

.. code-block:: text

  Your dataset (e.g. 100 items, batch_size=10)
    |
    v
  InputReader.__len__()  -->  returns 100
    |
    v
  Sky Batch splits into batches: [0-9], [10-19], ..., [90-99]
    |
    v
  For each batch, on a worker:
    |
    +-- InputReader.download_batch(start_idx, end_idx, cache_dir)
    |     --> returns List[Dict], e.g. [{"text": "hello"}, ...]
    |
    +-- Your mapper function receives the batch via sky.batch.load()
    |     --> processes it and calls sky.batch.save_results(results)
    |
    +-- OutputWriter.upload_batch(results, start_idx, end_idx, job_id)
          --> uploads the results to cloud storage
    |
    v
  After ALL batches finish:
    OutputWriter.reduce_results(job_id)
      --> optional: merge per-batch files into a single output
    OutputWriter.cleanup(job_id)
      --> optional: delete temporary batch files


Writing an InputReader
-----------------------

An InputReader tells Sky Batch how to read your input data. To create one:

1. Subclass :code:`io_formats.InputReader` as a :code:`@dataclass`
2. Register it with the format registry
3. Implement two methods: :code:`__len__` and :code:`download_batch`

.. code-block:: python

  from dataclasses import dataclass
  from sky.batch import io_formats
  from sky.utils import registry

  @registry.INPUT_READER_REGISTRY.type_register(name='my_reader')
  @dataclass
  class MyReader(io_formats.InputReader):
      my_option: str = 'default'

      def __len__(self) -> int:
          ...

      def download_batch(self, start_idx, end_idx, cache_dir) -> list:
          ...


__len__
~~~~~~~~

Return the **total number of items** in your dataset. Sky Batch uses this to decide how many batches to create.

For example, if :code:`__len__` returns 100 and :code:`batch_size=10`, Sky Batch creates 10 batches.

The indices ``0`` through ``len - 1`` form the global index space. You decide what each index means: a line number in a file, a row in a database, or just a counter.


download_batch
~~~~~~~~~~~~~~~

Called on a **worker** for each batch. Return the items for indices ``[start_idx, end_idx]`` (inclusive on both ends) as a list of dicts.

Parameters:

- :code:`start_idx` / :code:`end_idx`: The range within your global index space. For example, with 100 items and :code:`batch_size=10`, the first batch gets ``start_idx=0, end_idx=9``.
- :code:`cache_dir`: A local directory on the worker for caching. Multiple batches may run on the same worker, so you can download data once and reuse it across batches.
- **Return value**: A :code:`List[Dict]`, where each dict is one item. These dicts are exactly what your mapper function receives via :code:`sky.batch.load()`.


Example: RangeReader (no file I/O)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This reader generates items from a counter instead of reading a file:

.. code-block:: python

  @registry.INPUT_READER_REGISTRY.type_register(name='range')
  @dataclass
  class RangeReader(io_formats.InputReader):
      count: int

      def __len__(self):
          return self.count

      def download_batch(self, start_idx, end_idx, cache_dir):
          return [{'index': i} for i in range(start_idx, end_idx + 1)]

With :code:`RangeReader(path='', count=20)` and :code:`batch_size=5`, Sky Batch creates 4 batches. The first batch calls :code:`download_batch(0, 4, '/tmp/...')` and gets :code:`[{'index': 0}, {'index': 1}, ..., {'index': 4}]`.


Writing an OutputWriter
------------------------

An OutputWriter tells Sky Batch how to save your results. To create one:

1. Subclass :code:`io_formats.OutputWriter` as a :code:`@dataclass`
2. Register it with the format registry
3. Implement three methods: :code:`upload_batch`, :code:`reduce_results`, and :code:`cleanup`

.. code-block:: python

  @registry.OUTPUT_WRITER_REGISTRY.type_register(name='my_writer')
  @dataclass
  class MyWriter(io_formats.OutputWriter):
      column: str

      def upload_batch(self, results, start_idx, end_idx, job_id) -> str:
          ...

      def reduce_results(self, job_id) -> None:
          ...

      def cleanup(self, job_id) -> None:
          ...


upload_batch
~~~~~~~~~~~~~

Called on a **worker** after your mapper processes one batch. Upload the results to cloud storage and return the path written.

Parameters:

- :code:`results`: :code:`List[Dict]`, one dict per input item. These are the dicts your mapper passed to :code:`sky.batch.save_results()`.
- :code:`start_idx` / :code:`end_idx`: The same global indices from :code:`download_batch`. Useful for naming output files.
- :code:`job_id`: A unique string for this job run. Include it in temp file paths so concurrent jobs don't overwrite each other.
- :code:`self.path`: The output path the user specified when creating the writer.


reduce_results
~~~~~~~~~~~~~~~

Called **once** after all batches are complete. Use this to merge per-batch files into a single final output, or do nothing if your :code:`upload_batch` already writes to the final location.

.. note::

  :code:`reduce_results` and :code:`cleanup` run on the **jobs controller**, not on a GPU worker. The controller is typically a small CPU VM with limited memory. Avoid loading all results into memory at once. Stream through batch files one at a time instead.


cleanup
~~~~~~~~

Called after :code:`reduce_results`. Delete any temporary files your writer created during :code:`upload_batch`.


Common output patterns
~~~~~~~~~~~~~~~~~~~~~~~

There are two common patterns for output writers:

.. list-table::
   :header-rows: 1

   * - Pattern
     - upload_batch
     - reduce_results
     - cleanup
   * - **Per-item files**
     - Write one file per result to the final location.
     - No-op (:code:`pass`).
     - No-op (:code:`pass`).
   * - **Batch + merge**
     - Write batch results to a temp file.
     - Merge all temp files into the final output.
     - Delete temp files.


Example: per-item text files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each result becomes its own ``.txt`` file. Since files go directly to their final location, no reduce or cleanup is needed.

.. code-block:: python

  from sky.batch import utils

  @registry.OUTPUT_WRITER_REGISTRY.type_register(name='text')
  @dataclass
  class TextWriter(io_formats.OutputWriter):
      column: str

      def upload_batch(self, results, start_idx, end_idx, job_id):
          output_dir = self.path.rstrip('/')
          for i, result in enumerate(results):
              idx = start_idx + i
              text = str(result.get(self.column, ''))
              utils.upload_bytes_to_cloud(
                  text.encode('utf-8'), f'{output_dir}/{idx:08d}.txt')
          return output_dir

      def reduce_results(self, job_id):
          pass

      def cleanup(self, job_id):
          pass


Example: merged YAML file (batch + reduce)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each batch writes its results to a temporary file. After all batches finish, :code:`reduce_results` merges them into one YAML file, and :code:`cleanup` deletes the temp files.

.. code-block:: python

  from sky.batch import utils

  @registry.OUTPUT_WRITER_REGISTRY.type_register(name='yaml')
  @dataclass
  class YamlWriter(io_formats.OutputWriter):
      column: str

      def upload_batch(self, results, start_idx, end_idx, job_id):
          batch_path = utils.get_batch_path(
              self.path, start_idx, end_idx, job_id)
          filtered = [{self.column: r.get(self.column)} for r in results]
          utils.save_jsonl_to_cloud(filtered, batch_path)
          return batch_path

      def reduce_results(self, job_id):
          import yaml
          all_items = []
          for batch_path in utils.list_batch_files(self.path, job_id):
              all_items.extend(utils.load_jsonl_from_cloud(batch_path))
          yaml_bytes = yaml.dump(all_items, default_flow_style=False).encode()
          utils.upload_bytes_to_cloud(yaml_bytes, self.path)

      def cleanup(self, job_id):
          utils.delete_batch_files(self.path, job_id)
          utils.delete_input_batch_files(self.path, job_id)


Cloud storage utilities
------------------------

Sky Batch provides helper functions in :code:`sky.batch.utils` for common cloud storage operations. All functions support both ``s3://`` and ``gs://`` paths.

Writing data
~~~~~~~~~~~~~

- :code:`utils.upload_bytes_to_cloud(data, cloud_path)`: Upload raw bytes to a cloud path.
- :code:`utils.save_jsonl_to_cloud(items, cloud_path)`: Upload a list of dicts as a JSONL file.

Managing temporary batch files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When using the batch + merge pattern, store per-batch results under a ``.sky_batch_tmp/{job_id}/`` directory next to your output path. These utilities manage the paths for you:

- :code:`utils.get_batch_path(output_path, start_idx, end_idx, job_id)`: Generate a unique temp file path for one batch's results.
- :code:`utils.list_batch_files(output_path, job_id)`: List all temp batch files for a job, sorted by index.
- :code:`utils.load_jsonl_from_cloud(cloud_path)`: Download and parse a JSONL file.
- :code:`utils.concatenate_batches_to_output(output_path, job_id)`: Merge all batch files into a single output JSONL.

Cleaning up
~~~~~~~~~~~~

- :code:`utils.delete_batch_files(output_path, job_id)`: Delete all result batch files for a job.
- :code:`utils.delete_input_batch_files(output_path, job_id)`: Delete all input batch files for a job.


Recovering partial results on failure
--------------------------------------

If a job fails partway through, completed batch results are still in cloud storage. You can merge them manually from your laptop:

.. code-block:: python

  import sky.batch

  writer = sky.batch.JsonWriter(path='s3://bucket/output.jsonl')
  writer.reduce_results(job_id='42')
  writer.cleanup(job_id='42')  # Remove temporary batch files

The job ID is printed in the failure message. You can omit :code:`cleanup()` to keep the temp files for debugging.

For custom writers using the per-item pattern (where :code:`reduce_results` is a no-op), completed items are already in their final location. No recovery step is needed.


Complete example
-----------------

This example puts everything together: a custom :code:`RangeReader` that generates items from a counter, a :code:`TextWriter` that writes one ``.txt`` file per item, and a :code:`YamlWriter` that merges batch results into a single YAML file.

.. code-block:: python

  from dataclasses import dataclass
  from typing import Any, Dict, List

  import sky
  from sky.batch import io_formats
  from sky.batch import utils
  from sky.utils import registry

  # ---------------------------------------------------------------------------
  # Custom InputReader: generate items from a range (no file I/O)
  # ---------------------------------------------------------------------------

  @registry.INPUT_READER_REGISTRY.type_register(name='range')
  @dataclass
  class RangeReader(io_formats.InputReader):
      count: int

      def __len__(self) -> int:
          return self.count

      def download_batch(self, start_idx: int, end_idx: int,
                         cache_dir: str) -> List[Dict[str, Any]]:
          return [{'index': i} for i in range(start_idx, end_idx + 1)]

  # ---------------------------------------------------------------------------
  # Custom OutputWriter: per-item .txt files (no reduce needed)
  # ---------------------------------------------------------------------------

  @registry.OUTPUT_WRITER_REGISTRY.type_register(name='text')
  @dataclass
  class TextWriter(io_formats.OutputWriter):
      column: str

      def upload_batch(self, results: List[Dict[str, Any]], start_idx: int,
                       end_idx: int, job_id: str) -> str:
          output_dir = self.path.rstrip('/')
          for i, result in enumerate(results):
              global_idx = start_idx + i
              text = str(result.get(self.column, ''))
              utils.upload_bytes_to_cloud(
                  text.encode('utf-8'), f'{output_dir}/{global_idx:08d}.txt')
          return output_dir

      def reduce_results(self, job_id: str) -> None:
          pass

      def cleanup(self, job_id: str) -> None:
          pass

  # ---------------------------------------------------------------------------
  # Custom OutputWriter: single merged YAML file (batch + merge pattern)
  # ---------------------------------------------------------------------------

  @registry.OUTPUT_WRITER_REGISTRY.type_register(name='yaml')
  @dataclass
  class YamlWriter(io_formats.OutputWriter):
      column: str

      def upload_batch(self, results: List[Dict[str, Any]], start_idx: int,
                       end_idx: int, job_id: str) -> str:
          batch_path = utils.get_batch_path(self.path, start_idx, end_idx, job_id)
          filtered = [{self.column: r.get(self.column)} for r in results]
          utils.save_jsonl_to_cloud(filtered, batch_path)
          return batch_path

      def reduce_results(self, job_id: str) -> None:
          import yaml
          all_items: List[Dict[str, Any]] = []
          for batch_path in utils.list_batch_files(self.path, job_id):
              all_items.extend(utils.load_jsonl_from_cloud(batch_path))
          yaml_bytes = yaml.dump(all_items, default_flow_style=False).encode()
          utils.upload_bytes_to_cloud(yaml_bytes, self.path)

      def cleanup(self, job_id: str) -> None:
          utils.delete_batch_files(self.path, job_id)
          utils.delete_input_batch_files(self.path, job_id)

  # ---------------------------------------------------------------------------
  # Mapper function
  # ---------------------------------------------------------------------------

  @sky.batch.remote_function
  def process_items():
      import random

      for batch in sky.batch.load():
          results = []
          for item in batch:
              idx = item['index']
              tokens = [f'token_{j}' for j in range(idx, idx + 5)]
              text = f'Item {idx}: ' + ' | '.join(tokens)
              metadata = {
                  'id': idx,
                  'squared': idx ** 2,
                  'tag': random.choice(['alpha', 'beta', 'gamma']),
              }
              results.append({'text': text, 'metadata': metadata})
          sky.batch.save_results(results)

  # ---------------------------------------------------------------------------
  # Main
  # ---------------------------------------------------------------------------

  BUCKET = 's3://my-bucket'
  POOL_NAME = 'custom-fmt-pool'

  ds = sky.batch.Dataset(RangeReader(path='', count=20))

  ds.map(
      process_items,
      pool_name=POOL_NAME,
      batch_size=5,
      output=[
          TextWriter(f'{BUCKET}/output/texts/', column='text'),
          YamlWriter(f'{BUCKET}/output/metadata.yaml', column='metadata'),
      ],
  )

Run it:

.. code-block:: console

  $ sky jobs pool apply pool.yaml --pool custom-fmt-pool -y
  $ python process_range.py

After the job finishes, the output contains:

- 20 individual ``.txt`` files in ``s3://my-bucket/output/texts/``
- 1 merged ``metadata.yaml`` in ``s3://my-bucket/output/``
