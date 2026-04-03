# Writing Custom I/O Formats for Sky Batch

Sky Batch processes your data in **batches**: it splits your dataset into
smaller groups, sends each group to a worker, and collects the results.
To do this, it needs to know how to **read** your input data and how to
**write** your output data. That is what InputReader and OutputWriter do.

SkyPilot ships with built-in formats (`JsonInput`, `JsonOutput`,
`ImageOutput`), but you can define your own without modifying SkyPilot
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
       → uploads the results to cloud storage
  │
  ▼
After ALL batches finish:
  OutputWriter.reduce_results(job_id)
    → optional: merge per-batch files into a single output
```

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
class RangeInput(io_formats.InputReader):
    count: int

    def __len__(self):
        return self.count

    def download_batch(self, start_idx, end_idx, cache_dir):
        return [{'index': i} for i in range(start_idx, end_idx + 1)]
```

With `RangeInput(path='', count=20)` and `batch_size=5`, Sky Batch
creates 4 batches. The first batch calls
`download_batch(0, 4, '/tmp/...')` and gets
`[{'index': 0}, {'index': 1}, ..., {'index': 4}]`.

### Example: JSONL file with caching (built-in `JsonInput`)

This is how the built-in `JsonInput` reader works. It downloads the
full JSONL file from cloud storage on the first batch, caches it
locally, and then reads from the cache for subsequent batches on the
same worker. This avoids re-downloading the file for every batch.

```python
@registry.INPUT_READER_REGISTRY.type_register(name='json')
@dataclass
class JsonInput(io_formats.InputReader):

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
two methods:

```python
@registry.OUTPUT_WRITER_REGISTRY.type_register(name='my_writer')
@dataclass
class MyWriter(io_formats.OutputWriter):
    column: str

    def upload_batch(self, results, start_idx, end_idx, job_id) -> str: ...
    def reduce_results(self, job_id) -> None: ...
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

**Important:** `reduce_results` runs on the **jobs controller**, not on
a worker with GPUs. The controller is typically a small CPU VM with
limited memory. Avoid loading all results into memory at once if your
dataset is large. Instead, consider streaming through batch files one at
a time (read one, append to output, discard, read next). If your output
format requires all data in memory (e.g. sorting), be aware of the
memory limit and keep datasets at a reasonable size.

Two common patterns:

| Pattern | `upload_batch` | `reduce_results` |
|---------|---------------|-----------------|
| **Per-item files** | Write one file per result to the final location. | No-op (`pass`). |
| **Batch + merge** | Write batch results to a temp file. | Merge all temp files into the final output. |

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
per-batch results before merging. These helpers manage temp file paths
under a `.sky_batch_tmp/{job_id}/` directory next to your output path,
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
  A one-liner that lists all batch files, concatenates them in order
  into the final output JSONL file, and cleans up the temp files.
  This is what the built-in `JsonOutput` uses in its `reduce_results`.

### Example: per-item text files (no reduce needed)

Each result item becomes its own `.txt` file. Since files go directly
to their final location, `reduce_results` is a no-op.

```python
@registry.OUTPUT_WRITER_REGISTRY.type_register(name='text')
@dataclass
class TextOutput(io_formats.OutputWriter):
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
```

### Example: single merged YAML file (batch + reduce)

Each batch writes its results to a temporary file using
`utils.get_batch_path()`. After all batches finish, `reduce_results`
reads all temp files with `utils.list_batch_files()` and merges them
into one YAML file.

```python
@registry.OUTPUT_WRITER_REGISTRY.type_register(name='yaml')
@dataclass
class YamlOutput(io_formats.OutputWriter):
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
```

---

## Putting it together

```python
import sky.batch

# Define your custom formats (see examples above), then:
ds = sky.batch.Dataset(RangeInput(path='', count=20))
ds.map(
    my_mapper_fn,
    pool_name='my-pool',
    batch_size=5,
    output=[
        TextOutput('s3://bucket/texts/', column='text'),
        YamlOutput('s3://bucket/metadata.yaml', column='metadata'),
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

See `process_range.py` in this directory for the full runnable example.
