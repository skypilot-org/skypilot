"""Custom input/output formats for Sky Batch.

Demonstrates how to define custom formats outside of SkyPilot source code.
The base classes automatically embed this module's source in ``to_dict()``
so that remote workers can reconstruct the classes via ``from_dict()``.

Formats defined here:
- ``RangeInput``    — generates items from a Python range (no file I/O)
- ``TextOutput``    — writes a ``.txt`` file per item
- ``JsonFileOutput`` — writes a ``.json`` file per item
"""
import json
import logging
from typing import Any, Dict, List

from sky.batch import io_formats
from sky.batch import utils
from sky.utils import registry

logger = logging.getLogger(__name__)

# ---- Custom input format -----------------------------------------------------


@registry.INPUT_FORMAT_REGISTRY.type_register(name='range')
class RangeInput(io_formats.InputFormat):
    """Generate items from a Python ``range`` — no file I/O needed.

    Each item is a dict ``{'index': i}`` where *i* runs from 0 to
    *count* - 1.

    Args:
        count: Number of items to generate.
    """

    def __init__(self, count: int) -> None:
        super().__init__('')  # No cloud path needed
        self.count = count

    @classmethod
    def from_dict_args(cls, d: Dict[str, Any]) -> 'RangeInput':
        return cls(d['count'])

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d['count'] = self.count
        return d

    def count_items(self, dataset_path: str) -> int:
        return self.count

    def download_chunk(self, dataset_path: str, start_idx: int, end_idx: int,
                       cache_dir: str) -> List[Dict[str, Any]]:
        return [{'index': i} for i in range(start_idx, end_idx + 1)]

    def __repr__(self) -> str:
        return f'RangeInput(count={self.count})'


# ---- Custom output formats ---------------------------------------------------


@registry.OUTPUT_FORMAT_REGISTRY.type_register(name='text')
class TextOutput(io_formats.OutputFormat):
    """Per-item ``.txt`` file output.

    Writes one text file per result item to a cloud storage directory.
    Files are named ``{global_index:08d}.txt``.

    Args:
        path: Cloud storage directory (must end with ``/``).
        column: Key in result dicts that holds the text string.
    """

    def __init__(self, path: str, column: str = 'text') -> None:
        super().__init__(path)
        self.column = column
        self._validate()

    def _validate(self) -> None:
        if not self.path:
            raise ValueError('TextOutput path cannot be empty')
        if not self.path.startswith(('s3://', 'gs://')):
            raise ValueError(f'Unsupported storage path: {self.path}')
        if not self.path.endswith('/'):
            raise ValueError(f'TextOutput path must end with /: {self.path}')

    @classmethod
    def from_dict_args(cls, d: Dict[str, Any]) -> 'TextOutput':
        return cls(d['path'], column=d.get('column', 'text'))

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d['column'] = self.column
        return d

    def upload_chunk(self, results: List[Dict[str, Any]], output_path: str,
                     batch_idx: int, start_idx: int, end_idx: int,
                     job_id: str) -> str:
        output_dir = output_path.rstrip('/')
        for i, result in enumerate(results):
            global_idx = start_idx + i
            text = str(result.get(self.column, ''))
            cloud_path = f'{output_dir}/{global_idx:08d}.txt'
            utils.upload_bytes_to_cloud(text.encode('utf-8'), cloud_path)
        logger.info('Uploaded %d text files for batch %d', len(results),
                    batch_idx)
        return output_dir

    def merge_results(self, output_path: str, job_id: str) -> None:
        """No-op — files are already in their final location."""

    def __repr__(self) -> str:
        return f'TextOutput(path={self.path!r}, column={self.column!r})'


@registry.OUTPUT_FORMAT_REGISTRY.type_register(name='json_file')
class JsonFileOutput(io_formats.OutputFormat):
    """Per-item ``.json`` file output.

    Writes one JSON file per result item to a cloud storage directory.
    Files are named ``{global_index:08d}.json``.

    Args:
        path: Cloud storage directory (must end with ``/``).
        column: Key in result dicts that holds the value to serialize.
    """

    def __init__(self, path: str, column: str = 'metadata') -> None:
        super().__init__(path)
        self.column = column
        self._validate()

    def _validate(self) -> None:
        if not self.path:
            raise ValueError('JsonFileOutput path cannot be empty')
        if not self.path.startswith(('s3://', 'gs://')):
            raise ValueError(f'Unsupported storage path: {self.path}')
        if not self.path.endswith('/'):
            raise ValueError(
                f'JsonFileOutput path must end with /: {self.path}')

    @classmethod
    def from_dict_args(cls, d: Dict[str, Any]) -> 'JsonFileOutput':
        return cls(d['path'], column=d.get('column', 'metadata'))

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d['column'] = self.column
        return d

    def upload_chunk(self, results: List[Dict[str, Any]], output_path: str,
                     batch_idx: int, start_idx: int, end_idx: int,
                     job_id: str) -> str:
        output_dir = output_path.rstrip('/')
        for i, result in enumerate(results):
            global_idx = start_idx + i
            value = result.get(self.column, {})
            data = json.dumps(value, indent=2).encode('utf-8')
            cloud_path = f'{output_dir}/{global_idx:08d}.json'
            utils.upload_bytes_to_cloud(data, cloud_path)
        logger.info('Uploaded %d JSON files for batch %d', len(results),
                    batch_idx)
        return output_dir

    def merge_results(self, output_path: str, job_id: str) -> None:
        """No-op — files are already in their final location."""

    def __repr__(self) -> str:
        return f'JsonFileOutput(path={self.path!r}, column={self.column!r})'
