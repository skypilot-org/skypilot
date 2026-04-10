# Writing Custom I/O Formats for Sky Batch

Sky Batch processes your data in **batches**: it splits your dataset into
smaller groups, sends each group to a worker, and collects the results.
To do this, it needs to know how to **read** your input data and how to
**write** your output data. That is what InputReader and OutputWriter do.

SkyPilot ships with built-in formats (`JsonReader`, `JsonWriter`,
`ImageWriter`), but you can define your own without modifying SkyPilot
source code.

## How Sky Batch processes data

Here is the high-level flow:

```
Your dataset (e.g. 100 items, batch_size=10)
  │
  ▼
InputReader.__len__()  →  returns 100
  │
  ▼
Sky Batch splits into batches: [0-9], [10-19], ..., [90-99]
  │
  ▼
For each batch, on a worker:
  │
  ├─ InputReader.download_batch(start_idx, end_idx, cache_dir)
  │    → returns List[Dict], e.g. [{'text': 'hello'}, ...]
  │
  ├─ Your mapper function receives the batch via sky.batch.load()
  │    → processes it and calls sky.batch.save_results(results)
  │
  └─ OutputWriter.upload_batch(results, start_idx, end_idx, job_id)
  │    → uploads the results to cloud storage
  │
  ▼
After ALL batches finish (on the jobs controller):
  OutputWriter.reduce_results(job_id)
    → optional: merge per-batch files into a single output
  OutputWriter.cleanup(job_id)
    → optional: delete temporary batch files from cloud storage
```

See `process_range.py` in this directory for the full runnable example.

---

## InputReader

An InputReader tells Sky Batch how to read your input data. Subclass
`io_formats.InputReader` with `@dataclass`, register it, and implement
two methods:

```python
from dataclasses import dataclass
from sky.batch import io_formats
from sky.utils import registry

@registry.INPUT_READER_REGISTRY.type_register(name='my_reader')
@dataclass
class MyReader(io_formats.InputReader):
    my_option: str = 'default'    # extra fields after path

    def __len__(self) -> int: ...
    def download_batch(self, start_idx, end_idx, cache_dir) -> List[Dict]: ...
```

### `__len__(self) -> int`

Return the **total number of items** in your dataset. Sky Batch uses this
to decide how many batches to create. For example, if `__len__` returns
100 and `batch_size=10`, Sky Batch creates 10 batches.

The indices `0` through `len - 1` form the global index space. Every item
in your dataset corresponds to exactly one index. You decide what each
index means -- it could be a line number in a file, a row in a database,
or just a counter.

### `download_batch(self, start_idx, end_idx, cache_dir) -> List[Dict]`

Called on a **worker** for each batch. Your job is to return the
items for indices `[start_idx, end_idx]` (inclusive on both ends) as a
list of dicts.

- `start_idx` / `end_idx` -- the range within your global index space
  (from `__len__`). For example, with 100 items and `batch_size=10`,
  the first batch gets `start_idx=0, end_idx=9`, the second gets
  `start_idx=10, end_idx=19`, and so on.
- `cache_dir` -- a local directory on the worker you can use for caching.
  Multiple batches may run on the same worker, so you can download data
  once and reuse it across batches.
- **Return value** -- a `List[Dict]`, where each dict is one item. These
  dicts are exactly what your mapper function receives via
  `sky.batch.load()`. You control the dict keys -- they can be anything
  your mapper expects.

### Example: generating items from a range (no file I/O)

This reader does not read from any file. It simply generates items
based on a counter. `path` is required by the base class, so we pass
an empty string.

```python
@registry.INPUT_READER_REGISTRY.type_register(name='range')
@dataclass
class RangeReader(io_formats.InputReader):
    count: int

    def __len__(self):
        return self.count

    def download_batch(self, start_idx, end_idx, cache_dir):
        return [{'index': i} for i in range(start_idx, end_idx + 1)]
```

With `RangeReader(path='', count=20)` and `batch_size=5`, Sky Batch
creates 4 batches. The first batch calls
`download_batch(0, 4, '/tmp/...')` and gets
`[{'index': 0}, {'index': 1}, ..., {'index': 4}]`.

### Example: JSONL file with caching (built-in `JsonReader`)

This is how the built-in `JsonReader` reader works. Cloud storage APIs
(S3, GCS) only support downloading entire objects -- there is no way to
fetch just lines 10-19 from a single JSONL file. So `JsonReader`
downloads the full file on the first batch, caches it locally on the
worker, and reads from the cache for subsequent batches. If your dataset
is very large and you want each worker to only download the portion it
needs, split your data into multiple files and write a custom reader
that downloads only the relevant file(s) per batch.

```python
@registry.INPUT_READER_REGISTRY.type_register(name='json')
@dataclass
class JsonReader(io_formats.InputReader):

    def __len__(self):
        return len(utils.load_jsonl_from_cloud(self.path))

    def download_batch(self, start_idx, end_idx, cache_dir):
        # Cache the full dataset locally on first call.
        # Subsequent batches on the same worker skip the download.
        path_hash = hashlib.md5(self.path.encode()).hexdigest()
        cache_path = os.path.join(cache_dir, f'dataset_{path_hash}.jsonl')

        if not os.path.exists(cache_path):
            os.makedirs(cache_dir, exist_ok=True)
            full_data = utils.load_jsonl_from_cloud(self.path)
            with open(cache_path, 'w') as f:
                for item in full_data:
                    f.write(json.dumps(item) + '\n')

        # Read only the requested range from the cached file.
        data = []
        with open(cache_path, 'r') as f:
            for i, line in enumerate(f):
                if start_idx <= i <= end_idx:
                    data.append(json.loads(line.strip()))
                elif i > end_idx:
                    break
        return data
```

---

## OutputWriter

An OutputWriter tells Sky Batch how to save your results. Subclass
`io_formats.OutputWriter` with `@dataclass`, register it, and implement
three methods:

```python
@registry.OUTPUT_WRITER_REGISTRY.type_register(name='my_writer')
@dataclass
class MyWriter(io_formats.OutputWriter):
    column: str

    def upload_batch(self, results, start_idx, end_idx, job_id) -> str: ...
    def reduce_results(self, job_id) -> None: ...
    def cleanup(self, job_id) -> None: ...
```

### `upload_batch(results, start_idx, end_idx, job_id) -> str`

Called on a **worker** after your mapper processes one batch.
Upload the results to cloud storage and return the path written.

- `results` -- `List[Dict]`, one dict per input item in this batch.
  These are exactly the dicts your mapper passed to
  `sky.batch.save_results()`. The order matches the input batch.
- `start_idx` / `end_idx` -- the same global indices from
  `download_batch`. Useful for naming output files (e.g.
  `00000000.txt`, `00000001.txt`).
- `job_id` -- a unique string for this job run. Every call to
  `ds.map()` gets a different `job_id`. If you use the batch + merge
  pattern (writing temp files in `upload_batch`, merging in
  `reduce_results`), include `job_id` in your temp file paths so that
  multiple `ds.map()` calls writing to the same output bucket don't
  overwrite each other's intermediate files. The helper
  `utils.get_batch_path(self.path, start_idx, end_idx, job_id)`
  does this for you automatically.
- `self.path` -- the output path the user specified when creating the
  writer (e.g. `s3://bucket/output/`).

### `reduce_results(self, job_id) -> None`

Called **once** after all batches are complete. Use this to merge
per-batch files into a single final output, or do nothing if your
`upload_batch` already writes to the final location.

**Important:** Both `reduce_results` and `cleanup` run on the **jobs
controller**, not on a worker with GPUs. The controller is typically a
small CPU VM with limited memory. Avoid loading all results into memory
at once if your dataset is large. Instead, consider streaming through
batch files one at a time (read one, append to output, discard, read
next). If your output format requires all data in memory (e.g. sorting),
be aware of the memory limit and keep datasets at a reasonable size.

### `cleanup(self, job_id) -> None`

Called by the coordinator after `reduce_results` finishes. Delete any
temporary files your writer created during `upload_batch`.

If your writer uses the batch + merge pattern with
`utils.get_batch_path()`, call `utils.delete_batch_files()` and
`utils.delete_input_batch_files()` here. If your writer writes directly
to the final location (per-item pattern), just `pass`.

### Common patterns for `reduce_results` and `cleanup`

Two common patterns:

| Pattern | `upload_batch` | `reduce_results` | `cleanup` |
|---------|---------------|-----------------|-----------|
| **Per-item files** | Write one file per result to the final location. | No-op (`pass`). | No-op (`pass`). |
| **Batch + merge** | Write batch results to a temp file. | Merge all temp files into the final output. | Delete temp files. |

### Utility functions for cloud storage

Sky Batch provides helper functions in `sky.batch.utils` for common
cloud storage operations. You can use these in your `upload_batch` and
`reduce_results` implementations. All functions support both `s3://`
and `gs://` paths.

**Writing data to cloud storage:**

- `utils.upload_bytes_to_cloud(data: bytes, cloud_path: str)` --
  Upload raw bytes to a cloud path. Use this when you want full
  control over the file format (e.g. writing images, text files,
  or custom binary formats).
- `utils.save_jsonl_to_cloud(items: List[Dict], cloud_path: str)` --
  Upload a list of dicts as a JSONL file (one JSON object per line).
  Convenient for structured data.

**Managing temporary batch files (for the batch + merge pattern):**

When using the batch + merge pattern, you need a place to store
per-batch results before merging. The common practice is to store them
next to your output path under a `.sky_batch_tmp/{job_id}/` directory.
We provide utility functions that manage these temp file paths for you,
so multiple concurrent jobs don't interfere with each other:

- `utils.get_batch_path(output_path, start_idx, end_idx, job_id)` --
  Generate a unique temp file path for one batch's results. For
  example, with `output_path='s3://bucket/out.jsonl'` and
  `start_idx=0, end_idx=9, job_id='42'`, this returns something like
  `s3://bucket/.sky_batch_tmp/42/batch_00000000-00000009.jsonl`.
- `utils.list_batch_files(output_path, job_id)` --
  List all temp batch files for a given job, sorted by starting index.
  Use this in `reduce_results` to iterate over all batches in order.
- `utils.load_jsonl_from_cloud(cloud_path)` --
  Download and parse a JSONL file from cloud storage, returning a
  `List[Dict]`.
- `utils.concatenate_batches_to_output(output_path, job_id)` --
  A one-liner that lists all batch files and concatenates them in order
  into the final output JSONL file. This is what the built-in
  `JsonWriter` uses in its `reduce_results`. Temp file cleanup is
  handled separately by `cleanup()`.

**Cleaning up temporary files (for `cleanup`):**

- `utils.delete_batch_files(output_path, job_id)` --
  Delete all result batch files under `.sky_batch_tmp/{job_id}/` for
  the given output path.
- `utils.delete_input_batch_files(output_path, job_id)` --
  Delete all input batch files under `.sky_batch_tmp/{job_id}/` for
  the given output path.

### Example: per-item text files (no reduce needed)

Each result item becomes its own `.txt` file. Since files go directly
to their final location, `reduce_results` and `cleanup` are no-ops.

```python
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
        pass  # Files are already in the final location.

    def cleanup(self, job_id):
        pass  # No temp files to clean up.
```

### Example: single merged YAML file (batch + reduce)

Each batch writes its results to a temporary file using
`utils.get_batch_path()`. After all batches finish, `reduce_results`
reads all temp files with `utils.list_batch_files()` and merges them
into one YAML file, and `cleanup` then deletes the temp files.

```python
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
```

---

## Recovering partial results on failure

Normally, `reduce_results` and `cleanup` run on the **SkyPilot API Server**
after all batches complete. If the job fails partway through (e.g. a
worker crashes or gets preempted), those steps never run -- but the
per-batch results that workers already uploaded are still sitting in
cloud storage.

Because all intermediate results live in a shared cloud bucket (not on
any single machine), you can still retrieve data from a partially
finished run. Just run `reduce_results` and `cleanup` yourself from
anywhere -- your local laptop, a notebook, etc. This does the same
work the controller would have done, just manually. The output will
contain results from all batches that completed before the failure.
You can also run only `reduce_results` without `cleanup` to keep the
temp files around for inspection or debugging.

```python
import sky.batch

# Use the same output writer you passed to ds.map(), then call
# reduce_results + cleanup with the managed job ID (printed in the
# failure message).
writer = sky.batch.JsonWriter(path='s3://bucket/output.jsonl')
writer.reduce_results(job_id='42')
# Optionally, clean up the temp files.
writer.cleanup(job_id='42')
```

The managed job ID and the exact code snippet are printed when a batch
job fails, so you can copy-paste directly.

For custom output writers that use the batch + merge pattern,
`reduce_results` will merge all per-batch temporary files that were
uploaded before the failure, and `cleanup` will remove them. Writers
using the per-item pattern (where `reduce_results` is a no-op) already
have their completed items in the final location -- `cleanup` will be
a no-op too since there are no temp files to delete.

---

## Putting it together

```python
import sky.batch

# Define your custom formats (see examples above), then:
ds = sky.batch.Dataset(RangeReader(path='', count=20))
ds.map(
    my_mapper_fn,
    pool_name='my-pool',
    batch_size=5,
    output=[
        TextWriter('s3://bucket/texts/', column='text'),
        YamlWriter('s3://bucket/metadata.yaml', column='metadata'),
    ],
)
```

The mapper function receives batches via `sky.batch.load()` and saves
results via `sky.batch.save_results()`:

```python
@sky.batch.remote_function
def my_mapper_fn():
    for batch in sky.batch.load():
        # batch is a List[Dict] -- the return value of download_batch()
        results = []
        for item in batch:
            # process each item and build a result dict
            results.append({'text': ..., 'metadata': ...})
        # results is passed to upload_batch() for each OutputWriter
        sky.batch.save_results(results)
```
