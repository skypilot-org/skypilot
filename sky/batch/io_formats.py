"""Typed input/output format classes for Sky Batch.

Provides format classes aligned with Ray Data's API naming:

- ``JsonInput``  -> ``read_json``
- ``JsonOutput`` -> ``write_json``
- ``ImageOutput`` -> ``write_images(column=...)``

Each class is both a descriptor (path, to_dict/from_dict) and a handler
(__len__, download_batch, upload_batch, reduce_results).

"""
from abc import ABC
from abc import abstractmethod
import dataclasses
from dataclasses import dataclass
import hashlib
import inspect
import io
import json
import logging
import os
from typing import Any, Dict, List, Optional, Union

from sky.batch import utils
from sky.utils import registry

logger = logging.getLogger(__name__)


@dataclass
class InputReader(ABC):
    """Base class for input readers.

    Subclasses register via ``@registry.INPUT_READER_REGISTRY.type_register``.
    Custom readers defined outside this module are automatically serialized
    with their source code so they can be reconstructed on remote workers.

    Declare fields as dataclass fields. Serialization (``to_dict`` /
    ``from_dict_args``) is handled automatically -- subclasses only need
    to implement ``__len__`` and ``download_batch``.
    """

    path: str

    def _format_name(self) -> str:
        for name, cls in registry.INPUT_READER_REGISTRY.items():
            if cls is type(self):
                return name
        raise ValueError(f'Unregistered input reader: {type(self).__name__}')

    def _get_class_source(self) -> Optional[str]:
        """Return module source for custom (non-builtin) readers."""
        stored = getattr(self, '_class_source_code', None)
        if stored is not None:
            return stored
        if type(self).__module__ != __name__:
            try:
                source_file = inspect.getfile(type(self))
                with open(source_file, encoding='utf-8') as f:
                    return f.read()
            except (TypeError, OSError):
                return None
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this reader to a dict.

        Auto-generated via ``dataclasses.asdict``: every field with a
        non-None value is included.  Subclasses normally do **not**
        need to override this.
        """
        d = {k: v for k, v in dataclasses.asdict(self).items() if v is not None}
        d['format'] = self._format_name()
        class_source = self._get_class_source()
        if class_source is not None:
            d['_class_source'] = class_source
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> 'InputReader':
        """Reconstruct an InputReader from a dict."""
        fmt = d.get('format')
        class_source = d.get('_class_source')
        if class_source and fmt not in registry.INPUT_READER_REGISTRY:
            exec(  # pylint: disable=exec-used
                compile(class_source, '<custom_format>', 'exec'),
                {'__builtins__': __builtins__})
        cls = registry.INPUT_READER_REGISTRY.from_str(fmt)
        assert cls is not None, f'Unknown input reader: {fmt}'
        instance = cls.from_dict_args(d)
        if class_source:
            setattr(instance, '_class_source_code', class_source)
        return instance

    @classmethod
    def from_dict_args(cls, d: Dict[str, Any]) -> 'InputReader':
        """Construct an instance from a serialized dict.

        Auto-generated from dataclass fields.  Subclasses normally do
        **not** need to override this.
        """
        field_names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in field_names})

    @abstractmethod
    def __len__(self) -> int:
        """Return total number of items in the dataset."""

    @abstractmethod
    def download_batch(self, start_idx: int, end_idx: int,
                       cache_dir: str) -> List[Dict[str, Any]]:
        """Download data for a specific batch range."""


@dataclass
class OutputWriter(ABC):
    """Base class for output writers.

    Subclasses register via ``@registry.OUTPUT_WRITER_REGISTRY.type_register``.
    Custom writers defined outside this module are automatically serialized
    with their source code so they can be reconstructed on remote workers.

    Declare fields as dataclass fields. Serialization (``to_dict`` /
    ``from_dict_args``) is handled automatically -- subclasses only need
    to implement ``upload_batch`` and ``reduce_results``.
    """

    path: str

    def _format_name(self) -> str:
        for name, cls in registry.OUTPUT_WRITER_REGISTRY.items():
            if cls is type(self):
                return name
        raise ValueError(f'Unregistered output writer: {type(self).__name__}')

    def _get_class_source(self) -> Optional[str]:
        """Return module source for custom (non-builtin) writers."""
        stored = getattr(self, '_class_source_code', None)
        if stored is not None:
            return stored
        if type(self).__module__ != __name__:
            try:
                source_file = inspect.getfile(type(self))
                with open(source_file, encoding='utf-8') as f:
                    return f.read()
            except (TypeError, OSError):
                return None
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this writer to a dict.

        Auto-generated via ``dataclasses.asdict``: every field with a
        non-None value is included.  Subclasses normally do **not**
        need to override this.
        """
        d = {k: v for k, v in dataclasses.asdict(self).items() if v is not None}
        d['format'] = self._format_name()
        class_source = self._get_class_source()
        if class_source is not None:
            d['_class_source'] = class_source
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> 'OutputWriter':
        """Reconstruct an OutputWriter from a dict."""
        fmt = d.get('format')
        class_source = d.get('_class_source')
        if class_source and fmt not in registry.OUTPUT_WRITER_REGISTRY:
            exec(  # pylint: disable=exec-used
                compile(class_source, '<custom_format>', 'exec'),
                {'__builtins__': __builtins__})
        cls = registry.OUTPUT_WRITER_REGISTRY.from_str(fmt)
        assert cls is not None, f'Unknown output writer: {fmt}'
        instance = cls.from_dict_args(d)
        if class_source:
            setattr(instance, '_class_source_code', class_source)
        return instance

    @classmethod
    def from_dict_args(cls, d: Dict[str, Any]) -> 'OutputWriter':
        """Construct an instance from a serialized dict.

        Auto-generated from dataclass fields.  Subclasses normally do
        **not** need to override this.
        """
        field_names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in field_names})

    @abstractmethod
    def upload_batch(self, results: List[Dict[str, Any]], start_idx: int,
                     end_idx: int, job_id: str) -> str:
        """Upload results for a specific batch."""

    @abstractmethod
    def reduce_results(self, job_id: str) -> None:
        """Reduce all result batches into final output."""

    @abstractmethod
    def cleanup(self, job_id: str) -> None:
        """Delete temporary batch files after reduce_results completes."""


# ---- Concrete input readers --------------------------------------------------


@registry.INPUT_READER_REGISTRY.type_register(name='json')
@dataclass
class JsonInput(InputReader):
    """JSONL input reader.

    Corresponds to Ray Data's ``read_json``.

    Args:
        path: Cloud storage path to a ``.jsonl`` file.
              Supported prefixes: ``s3://``, ``gs://``.
    """

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError('JsonInput path cannot be empty')
        supported_prefixes = ('s3://', 'gs://')
        if not self.path.startswith(supported_prefixes):
            raise ValueError(
                f'Unsupported storage path: {self.path}. '
                f'Supported prefixes: {", ".join(supported_prefixes)}')
        if not self.path.endswith('.jsonl'):
            raise ValueError(
                f'JsonInput path must end with .jsonl: {self.path}')

    def __len__(self) -> int:
        return len(utils.load_jsonl_from_cloud(self.path))

    def download_batch(self, start_idx: int, end_idx: int,
                       cache_dir: str) -> List[Dict[str, Any]]:
        path_hash = hashlib.md5(self.path.encode()).hexdigest()
        cache_path = os.path.join(cache_dir, f'dataset_{path_hash}.jsonl')

        if not os.path.exists(cache_path):
            os.makedirs(cache_dir, exist_ok=True)
            utils.download_from_cloud(self.path, cache_path)

        data = []
        with open(cache_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                if i >= start_idx and i <= end_idx:
                    data.append(json.loads(line.strip()))
                elif i > end_idx:
                    break
        return data


# ---- Concrete output writers -------------------------------------------------


@registry.OUTPUT_WRITER_REGISTRY.type_register(name='json')
@dataclass
class JsonOutput(OutputWriter):
    """JSONL output writer.

    Corresponds to Ray Data's ``write_json``.

    Args:
        path: Cloud storage path for the output ``.jsonl`` file.
              Supported prefixes: ``s3://``, ``gs://``.
        column: Optional list of keys (or single key string) to include
                from each result dict. When ``None`` (default), all fields
                are written (backward compatible).
    """

    column: Optional[Union[str, List[str]]] = None

    def __post_init__(self) -> None:
        if isinstance(self.column, str):
            self.column = [self.column]
        if not self.path:
            raise ValueError('JsonOutput path cannot be empty')
        supported_prefixes = ('s3://', 'gs://')
        if not self.path.startswith(supported_prefixes):
            raise ValueError(
                f'Unsupported storage path: {self.path}. '
                f'Supported prefixes: {", ".join(supported_prefixes)}')
        if not self.path.endswith('.jsonl'):
            raise ValueError(
                f'JsonOutput path must end with .jsonl: {self.path}')

    def upload_batch(self, results: List[Dict[str, Any]], start_idx: int,
                     end_idx: int, job_id: str) -> str:
        batch_path = utils.get_batch_path(self.path, start_idx, end_idx, job_id)
        if self.column is not None:
            results = [{k: r[k] for k in self.column if k in r} for r in results
                      ]
        utils.save_jsonl_to_cloud(results, batch_path)
        return batch_path

    def reduce_results(self, job_id: str) -> None:
        utils.concatenate_batches_to_output(self.path, job_id)

    def cleanup(self, job_id: str) -> None:
        utils.delete_batch_files(self.path, job_id)
        utils.delete_input_batch_files(self.path, job_id)


@registry.OUTPUT_WRITER_REGISTRY.type_register(name='image')
@dataclass
class ImageOutput(OutputWriter):
    """Image directory output writer.

    Corresponds to Ray Data's ``write_images(column=...)``.

    Args:
        path: Cloud storage directory path for output images.
              Must end with ``/``.
              Supported prefixes: ``s3://``, ``gs://``.
        column: Name of the key in result dicts that holds the PIL Image.
                Defaults to ``'image'``.
    """

    column: str = 'image'

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError('ImageOutput path cannot be empty')
        supported_prefixes = ('s3://', 'gs://')
        if not self.path.startswith(supported_prefixes):
            raise ValueError(
                f'Unsupported storage path: {self.path}. '
                f'Supported prefixes: {", ".join(supported_prefixes)}')
        if not self.path.endswith('/'):
            raise ValueError(f'ImageOutput path must end with /: {self.path}')

    def upload_batch(self, results: List[Dict[str, Any]], start_idx: int,
                     end_idx: int, job_id: str) -> str:
        output_dir = self.path.rstrip('/')

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

        logger.info('Uploaded %d images [%d-%d]', len(results), start_idx,
                    end_idx)
        return output_dir

    def reduce_results(self, job_id: str) -> None:
        """No-op -- images are already in their final location."""

    def cleanup(self, job_id: str) -> None:
        """No-op -- no temp files to clean up."""
