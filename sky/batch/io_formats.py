"""Typed input/output format classes for Sky Batch.

Provides format classes aligned with Ray Data's API naming:

- ``JsonInput``  -> ``read_json``
- ``JsonOutput`` -> ``write_json``
- ``ImageOutput`` -> ``write_images(column=...)``

Each class is both a descriptor (path, to_dict/from_dict) and a handler
(count_items, download_chunk, upload_chunk, merge_results).
"""
from abc import ABC
from abc import abstractmethod
import hashlib
import io
import json
import logging
import os
from typing import Any, Dict, List, Optional, Union

from sky.batch import utils

logger = logging.getLogger(__name__)


class InputFormat(ABC):
    """Base class for input formats."""

    def __init__(self, path: str):
        self.path = path

    def to_dict(self) -> Dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> 'InputFormat':
        """Reconstruct an InputFormat from a dict."""
        fmt = d.get('format')
        if fmt == 'json':
            return JsonInput(d['path'])
        raise ValueError(f'Unknown input format: {fmt}')

    @abstractmethod
    def count_items(self, dataset_path: str) -> int:
        """Count total items in the dataset."""

    @abstractmethod
    def get_metadata(self, dataset_path: str) -> Dict[str, Any]:
        """Get dataset metadata without downloading data."""

    @abstractmethod
    def download_chunk(self, dataset_path: str, start_idx: int, end_idx: int,
                       cache_dir: str) -> List[Dict[str, Any]]:
        """Download data for a specific chunk range."""

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(path={self.path!r})'


class OutputFormat(ABC):
    """Base class for output formats."""

    def __init__(self, path: str):
        self.path = path

    def to_dict(self) -> Dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> 'OutputFormat':
        """Reconstruct an OutputFormat from a dict."""
        fmt = d.get('format')
        if fmt == 'json':
            return JsonOutput(d['path'], column=d.get('column'))
        elif fmt == 'image':
            return ImageOutput(d['path'], column=d.get('column', 'image'))
        raise ValueError(f'Unknown output format: {fmt}')

    @abstractmethod
    def upload_chunk(self, results: List[Dict[str, Any]], output_path: str,
                     batch_idx: int, start_idx: int, end_idx: int,
                     job_id: str) -> str:
        """Upload results for a specific chunk."""

    @abstractmethod
    def merge_results(self, output_path: str, job_id: str) -> None:
        """Merge all result chunks into final output."""

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(path={self.path!r})'


# ---- Concrete input formats ------------------------------------------------


class JsonInput(InputFormat):
    """JSONL input format.

    Corresponds to Ray Data's ``read_json``.

    Args:
        path: Cloud storage path to a ``.jsonl`` file.
              Supported prefixes: ``s3://``, ``gs://``, ``r2://``.
    """

    def __init__(self, path: str):
        super().__init__(path)
        self._validate()

    def _validate(self) -> None:
        if not self.path:
            raise ValueError('JsonInput path cannot be empty')
        supported_prefixes = ('s3://', 'gs://', 'r2://')
        if not self.path.startswith(supported_prefixes):
            raise ValueError(
                f'Unsupported storage path: {self.path}. '
                f'Supported prefixes: {", ".join(supported_prefixes)}')
        if not self.path.endswith('.jsonl'):
            raise ValueError(
                f'JsonInput path must end with .jsonl: {self.path}')

    def to_dict(self) -> Dict[str, Any]:
        return {'format': 'json', 'path': self.path}

    # -- InputDatasetFormat implementation ----------------------------------

    def count_items(self, dataset_path: str) -> int:
        data = utils.load_jsonl_from_cloud(dataset_path)
        return len(data)

    def get_metadata(self, dataset_path: str) -> Dict[str, Any]:
        total_items = self.count_items(dataset_path)
        return {
            'total_items': total_items,
            'format': 'jsonl',
            'path': dataset_path,
        }

    def download_chunk(self, dataset_path: str, start_idx: int, end_idx: int,
                       cache_dir: str) -> List[Dict[str, Any]]:
        cache_filename = self._get_cache_filename(dataset_path)
        cache_path = os.path.join(cache_dir, cache_filename)

        if not os.path.exists(cache_path):
            os.makedirs(cache_dir, exist_ok=True)
            full_data = utils.load_jsonl_from_cloud(dataset_path)
            with open(cache_path, 'w', encoding='utf-8') as f:
                for item in full_data:
                    f.write(json.dumps(item) + '\n')

        data = []
        with open(cache_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                if i >= start_idx and i <= end_idx:
                    data.append(json.loads(line.strip()))
                elif i > end_idx:
                    break
        return data

    @staticmethod
    def _get_cache_filename(dataset_path: str) -> str:
        path_hash = hashlib.md5(dataset_path.encode()).hexdigest()
        return f'dataset_{path_hash}.jsonl'


# ---- Concrete output formats -----------------------------------------------


class JsonOutput(OutputFormat):
    """JSONL output format.

    Corresponds to Ray Data's ``write_json``.

    Args:
        path: Cloud storage path for the output ``.jsonl`` file.
              Supported prefixes: ``s3://``, ``gs://``, ``r2://``.
        column: Optional list of keys (or single key string) to include
                from each result dict. When ``None`` (default), all fields
                are written (backward compatible).
    """

    def __init__(self,
                 path: str,
                 column: Optional[Union[str, List[str]]] = None):
        super().__init__(path)
        # Normalize column to a list or None.
        if isinstance(column, str):
            self.column: Optional[List[str]] = [column]
        else:
            self.column = column
        self._validate()

    def _validate(self) -> None:
        if not self.path:
            raise ValueError('JsonOutput path cannot be empty')
        supported_prefixes = ('s3://', 'gs://', 'r2://')
        if not self.path.startswith(supported_prefixes):
            raise ValueError(
                f'Unsupported storage path: {self.path}. '
                f'Supported prefixes: {", ".join(supported_prefixes)}')
        if not self.path.endswith('.jsonl'):
            raise ValueError(
                f'JsonOutput path must end with .jsonl: {self.path}')

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {'format': 'json', 'path': self.path}
        if self.column is not None:
            d['column'] = self.column
        return d

    # -- OutputDatasetFormat implementation ---------------------------------

    def _filter_columns(self, results: List[Dict[str,
                                                 Any]]) -> List[Dict[str, Any]]:
        """Filter result dicts to only include specified columns."""
        if self.column is None:
            return results
        return [{k: r[k] for k in self.column if k in r} for r in results]

    def upload_chunk(self, results: List[Dict[str, Any]], output_path: str,
                     batch_idx: int, start_idx: int, end_idx: int,
                     job_id: str) -> str:
        chunk_path = utils.get_chunk_path(output_path, start_idx, end_idx,
                                          job_id)
        filtered = self._filter_columns(results)
        utils.save_jsonl_to_cloud(filtered, chunk_path)
        return chunk_path

    def merge_results(self, output_path: str, job_id: str) -> None:
        utils.concatenate_chunks_to_output(output_path, job_id)

    def __repr__(self) -> str:
        if self.column is not None:
            return (f'JsonOutput(path={self.path!r}, '
                    f'column={self.column!r})')
        return f'JsonOutput(path={self.path!r})'


class ImageOutput(OutputFormat):
    """Image directory output format.

    Corresponds to Ray Data's ``write_images(column=...)``.

    Args:
        path: Cloud storage directory path for output images.
              Must end with ``/``.
              Supported prefixes: ``s3://``, ``gs://``, ``r2://``.
        column: Name of the key in result dicts that holds the PIL Image.
                Defaults to ``'image'``.
    """

    def __init__(self, path: str, column: str = 'image'):
        super().__init__(path)
        self.column = column
        self._validate()

    def _validate(self) -> None:
        if not self.path:
            raise ValueError('ImageOutput path cannot be empty')
        supported_prefixes = ('s3://', 'gs://', 'r2://')
        if not self.path.startswith(supported_prefixes):
            raise ValueError(
                f'Unsupported storage path: {self.path}. '
                f'Supported prefixes: {", ".join(supported_prefixes)}')
        if not self.path.endswith('/'):
            raise ValueError(f'ImageOutput path must end with /: {self.path}')

    def to_dict(self) -> Dict[str, Any]:
        return {
            'format': 'image',
            'path': self.path,
            'column': self.column,
        }

    # -- OutputDatasetFormat implementation ---------------------------------

    def upload_chunk(self, results: List[Dict[str, Any]], output_path: str,
                     batch_idx: int, start_idx: int, end_idx: int,
                     job_id: str) -> str:
        output_dir = output_path.rstrip('/')

        for i, result in enumerate(results):
            global_idx = start_idx + i
            value = result.get(self.column)
            if value is None or not hasattr(value, 'save'):
                logger.warning(
                    'Result %d missing PIL Image in column %r, skipping',
                    global_idx, self.column)
                continue

            image_filename = f'{global_idx:08d}.png'
            image_cloud_path = f'{output_dir}/{image_filename}'

            buf = io.BytesIO()
            value.save(buf, format='PNG')
            buf.seek(0)
            utils.upload_bytes_to_cloud(buf.read(), image_cloud_path)
            logger.debug('Uploaded image %s', image_cloud_path)

        logger.info('Uploaded %d images for batch %d', len(results), batch_idx)
        return output_dir

    def merge_results(self, output_path: str, job_id: str) -> None:
        """No-op — images are already in their final location."""
        logger.info('Images already in final location: %s', output_path)

    def __repr__(self) -> str:
        return (f'ImageOutput(path={self.path!r}, '
                f'column={self.column!r})')
