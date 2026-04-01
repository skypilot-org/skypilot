# Writing Custom I/O Formats for Sky Batch

SkyPilot ships with built-in formats (`JsonInput`, `JsonOutput`,
`ImageOutput`), but you can define your own without modifying SkyPilot
source code. This guide shows how to implement each interface method, with
realistic examples.

## InputFormat

Subclass `io_formats.InputFormat` and implement four methods:

```python
from sky.batch import io_formats
from sky.utils import registry

@registry.INPUT_FORMAT_REGISTRY.type_register(name='my_format')
class MyInput(io_formats.InputFormat):
    def __init__(self, path, ...)        # Store config, call super().__init__(path)
    def from_dict_args(cls, d) -> Self   # classmethod: reconstruct from dict
    def to_dict(self) -> dict            # serialize (override only if you have extra fields)
    def count_items(self, dataset_path) -> int
    def download_chunk(self, dataset_path, start_idx, end_idx, cache_dir) -> List[Dict]
```

### `count_items(self, dataset_path: str) -> int`

Return the total number of items in the dataset. This determines how many
batches are created.

### `download_chunk(self, dataset_path, start_idx, end_idx, cache_dir) -> List[Dict]`

Return the items for indices `[start_idx, end_idx]` (inclusive). Each item
is a dict that the mapper function receives.

- `cache_dir` is a local directory you can use to avoid re-downloading the
  same data when multiple batches run on the same worker.

### `to_dict` / `from_dict_args`

These serialize and deserialize your format so it can be sent to remote
workers as JSON.

The base class `to_dict()` already produces `{'format': '<name>', 'path': self.path}`.
**If your format only needs `path`, you don't need to override `to_dict`
at all** (see the `MyJsonlInput` example below). Override it only when you
have extra constructor arguments (like `count` or `column`) — call
`super().to_dict()` and add your fields to the returned dict.

`from_dict_args` is the inverse: given the dict, construct an instance.
It is always required since the base class doesn't know your constructor
signature.

### Example: reading a JSONL file from cloud storage

This re-implements the built-in `JsonInput` as a custom format:

```python
import hashlib, json, os
from typing import Any, Dict, List
from sky.batch import io_formats, utils
from sky.utils import registry


@registry.INPUT_FORMAT_REGISTRY.type_register(name='my_jsonl')
class MyJsonlInput(io_formats.InputFormat):

    def __init__(self, path: str) -> None:
        super().__init__(path)

    @classmethod
    def from_dict_args(cls, d: Dict[str, Any]) -> 'MyJsonlInput':
        return cls(d['path'])

    # to_dict() inherited — base class produces {'format': 'my_jsonl', 'path': ...}

    def count_items(self, dataset_path: str) -> int:
        return len(utils.load_jsonl_from_cloud(dataset_path))

    def download_chunk(self, dataset_path: str, start_idx: int,
                       end_idx: int,
                       cache_dir: str) -> List[Dict[str, Any]]:
        # Cache the full file locally so subsequent batches are instant.
        path_hash = hashlib.md5(dataset_path.encode()).hexdigest()
        cache_path = os.path.join(cache_dir, f'dataset_{path_hash}.jsonl')

        if not os.path.exists(cache_path):
            os.makedirs(cache_dir, exist_ok=True)
            full_data = utils.load_jsonl_from_cloud(dataset_path)
            with open(cache_path, 'w') as f:
                for item in full_data:
                    f.write(json.dumps(item) + '\n')

        items = []
        with open(cache_path) as f:
            for i, line in enumerate(f):
                if start_idx <= i <= end_idx:
                    items.append(json.loads(line))
                elif i > end_idx:
                    break
        return items
```

### Example: generating items from a range (no file I/O)

For inputs derived from parameters rather than files (hyperparameter sweeps,
prompt templates, index ranges):

```python
@registry.INPUT_FORMAT_REGISTRY.type_register(name='range')
class RangeInput(io_formats.InputFormat):

    def __init__(self, count: int) -> None:
        super().__init__('')   # No cloud path
        self.count = count

    @classmethod
    def from_dict_args(cls, d):
        return cls(d['count'])

    def to_dict(self):
        d = super().to_dict()
        d['count'] = self.count
        return d

    def count_items(self, dataset_path):
        return self.count

    def download_chunk(self, dataset_path, start_idx, end_idx, cache_dir):
        return [{'index': i} for i in range(start_idx, end_idx + 1)]
```

---

## OutputFormat

Subclass `io_formats.OutputFormat` and implement four methods:

```python
@registry.OUTPUT_FORMAT_REGISTRY.type_register(name='my_output')
class MyOutput(io_formats.OutputFormat):
    def __init__(self, path, ...)        # Store config, call super().__init__(path)
    def from_dict_args(cls, d) -> Self   # classmethod: reconstruct from dict
    def to_dict(self) -> dict            # serialize (override only if you have extra fields)
    def upload_chunk(self, results, output_path, batch_idx, start_idx, end_idx, job_id) -> str
    def merge_results(self, output_path, job_id) -> None
```

### `upload_chunk(self, results, output_path, batch_idx, start_idx, end_idx, job_id) -> str`

Upload results for one batch and return the path written.

- `results` — `List[Dict]`, one dict per input item in this batch.
- `start_idx` / `end_idx` — global indices (useful for naming output files).
- `job_id` — use it to namespace temp files so concurrent jobs don't collide.

There are two common patterns:

| Pattern | How it works | `merge_results` |
|---------|-------------|-----------------|
| **Chunk + merge** | Write all batch results to a single temp chunk file. | Concatenate all chunks into the final output. |
| **Per-item files** | Write one file per result directly to the final location. | No-op. |

### `merge_results(self, output_path: str, job_id: str) -> None`

Called once after all batches complete. Combine chunks into the final output,
or do nothing if files are already in their final location.

### `to_dict` / `from_dict_args`

Same rules as InputFormat. The base class provides
`{'format': '<name>', 'path': self.path}`. Override `to_dict` only if you
have extra fields (like `column`). `from_dict_args` is always required.

### Example: writing a single JSONL file (chunk + merge)

This re-implements the built-in `JsonOutput`:

```python
from typing import Any, Dict, List, Optional, Union
from sky.batch import io_formats, utils
from sky.utils import registry


@registry.OUTPUT_FORMAT_REGISTRY.type_register(name='my_jsonl')
class MyJsonlOutput(io_formats.OutputFormat):

    def __init__(self, path: str,
                 column: Optional[Union[str, List[str]]] = None):
        super().__init__(path)
        self.column = [column] if isinstance(column, str) else column

    @classmethod
    def from_dict_args(cls, d: Dict[str, Any]) -> 'MyJsonlOutput':
        return cls(d['path'], column=d.get('column'))

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        if self.column is not None:
            d['column'] = self.column
        return d

    def upload_chunk(self, results: List[Dict[str, Any]],
                     output_path: str, batch_idx: int,
                     start_idx: int, end_idx: int,
                     job_id: str) -> str:
        # Write to a temp chunk path (not the final output).
        chunk_path = utils.get_chunk_path(
            output_path, start_idx, end_idx, job_id)

        if self.column is not None:
            results = [{k: r[k] for k in self.column if k in r}
                       for r in results]

        utils.save_jsonl_to_cloud(results, chunk_path)
        return chunk_path

    def merge_results(self, output_path: str, job_id: str) -> None:
        # Concatenate all chunk files into the final JSONL, then clean up.
        utils.concatenate_chunks_to_output(output_path, job_id)
```

### Example: writing per-item text files (no merge)

```python
@registry.OUTPUT_FORMAT_REGISTRY.type_register(name='text')
class TextOutput(io_formats.OutputFormat):

    def __init__(self, path: str, column: str = 'text') -> None:
        super().__init__(path)
        self.column = column

    @classmethod
    def from_dict_args(cls, d):
        return cls(d['path'], column=d.get('column', 'text'))

    def to_dict(self):
        d = super().to_dict()
        d['column'] = self.column
        return d

    def upload_chunk(self, results, output_path, batch_idx,
                     start_idx, end_idx, job_id):
        output_dir = output_path.rstrip('/')
        for i, result in enumerate(results):
            global_idx = start_idx + i
            text = str(result.get(self.column, ''))
            cloud_path = f'{output_dir}/{global_idx:08d}.txt'
            utils.upload_bytes_to_cloud(text.encode('utf-8'), cloud_path)
        return output_dir

    def merge_results(self, output_path, job_id):
        pass  # Files are already in the final location.
```

---

## Putting it together

```python
import sky.batch
from custom_formats import RangeInput, TextOutput, JsonFileOutput

ds = sky.batch.Dataset(RangeInput(count=20))
ds.map(
    my_mapper_fn,
    pool_name='my-pool',
    batch_size=5,
    output=[
        TextOutput('s3://bucket/texts/', column='text'),
        JsonFileOutput('s3://bucket/meta/', column='metadata'),
    ],
)
```

See `process_range.py` in this directory for the full runnable example.
