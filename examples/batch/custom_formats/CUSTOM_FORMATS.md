# Writing Custom I/O Formats for Sky Batch

SkyPilot ships with built-in formats (`JsonInput`, `JsonOutput`,
`ImageOutput`), but you can define your own without modifying SkyPilot
source code.

## InputFormat

Subclass `io_formats.InputFormat` with `@dataclass`, register it, and
implement two methods:

```python
from dataclasses import dataclass
from sky.batch import io_formats
from sky.utils import registry

@registry.INPUT_FORMAT_REGISTRY.type_register(name='my_format')
@dataclass
class MyInput(io_formats.InputFormat):
    my_option: str = 'default'    # extra fields (must have defaults)

    def count_items(self, dataset_path: str) -> int: ...
    def download_chunk(self, dataset_path, start_idx, end_idx, cache_dir) -> List[Dict]: ...
```

### `count_items(self, dataset_path: str) -> int`

Return the total number of items. This determines how many batches are
created.

### `download_chunk(self, dataset_path, start_idx, end_idx, cache_dir) -> List[Dict]`

Return items for indices `[start_idx, end_idx]` (inclusive). Each item
is a dict that the mapper function receives.

`cache_dir` is a local directory you can use to avoid re-downloading
when multiple batches run on the same worker.

### Example: generating items from a range (no file I/O)

```python
@registry.INPUT_FORMAT_REGISTRY.type_register(name='range')
@dataclass
class RangeInput(io_formats.InputFormat):
    count: int = 0

    def count_items(self, dataset_path):
        return self.count

    def download_chunk(self, dataset_path, start_idx, end_idx, cache_dir):
        return [{'index': i} for i in range(start_idx, end_idx + 1)]
```

> **Note:** The base class declares `path: str = ''` with a default, so
> all subclass fields must also have defaults (Python `@dataclass`
> inheritance rule). Use `__post_init__` to validate required fields.

---

## OutputFormat

Subclass `io_formats.OutputFormat` with `@dataclass`, register it, and
implement two methods:

```python
@registry.OUTPUT_FORMAT_REGISTRY.type_register(name='my_output')
@dataclass
class MyOutput(io_formats.OutputFormat):
    column: str = 'text'

    def upload_chunk(self, results, output_path, batch_idx, start_idx, end_idx, job_id) -> str: ...
    def merge_results(self, output_path, job_id) -> None: ...
```

### `upload_chunk(...) -> str`

Upload results for one batch and return the path written.

- `results` -- `List[Dict]`, one dict per input item in this batch.
- `start_idx` / `end_idx` -- global indices (useful for naming files).
- `job_id` -- namespace temp files so concurrent jobs don't collide.

### `merge_results(self, output_path, job_id) -> None`

Called once after all batches complete. Two common patterns:

| Pattern | `upload_chunk` | `merge_results` |
|---------|---------------|-----------------|
| **Per-item files** | Write one file per result to the final location. | No-op (`pass`). |
| **Chunk + merge** | Write batch results to a temp chunk file. | Concatenate chunks into the final output. |

### Example: per-item text files (no merge needed)

```python
@registry.OUTPUT_FORMAT_REGISTRY.type_register(name='text')
@dataclass
class TextOutput(io_formats.OutputFormat):
    column: str = 'text'

    def upload_chunk(self, results, output_path, batch_idx,
                     start_idx, end_idx, job_id):
        output_dir = output_path.rstrip('/')
        for i, result in enumerate(results):
            idx = start_idx + i
            text = str(result.get(self.column, ''))
            utils.upload_bytes_to_cloud(
                text.encode('utf-8'), f'{output_dir}/{idx:08d}.txt')
        return output_dir

    def merge_results(self, output_path, job_id):
        pass  # Files are already in the final location.
```

### Example: single merged YAML file (chunk + merge)

```python
@registry.OUTPUT_FORMAT_REGISTRY.type_register(name='yaml')
@dataclass
class YamlOutput(io_formats.OutputFormat):
    column: str = 'metadata'

    def upload_chunk(self, results, output_path, batch_idx,
                     start_idx, end_idx, job_id):
        # Write batch results to a temporary JSONL chunk.
        chunk_path = utils.get_chunk_path(
            output_path, start_idx, end_idx, job_id)
        filtered = [{self.column: r.get(self.column)} for r in results]
        utils.save_jsonl_to_cloud(filtered, chunk_path)
        return chunk_path

    def merge_results(self, output_path, job_id):
        import yaml
        # Read all chunks, combine, write as a single YAML file.
        all_items = []
        for chunk_path in utils.list_chunk_files(output_path, job_id):
            all_items.extend(utils.load_jsonl_from_cloud(chunk_path))
        yaml_bytes = yaml.dump(all_items, default_flow_style=False).encode()
        utils.upload_bytes_to_cloud(yaml_bytes, output_path)
```

---

## Putting it together

```python
import sky.batch

# Define your custom formats (see examples above), then:
ds = sky.batch.Dataset(RangeInput(count=20))
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

See `process_range.py` in this directory for the full runnable example.
