"""Typed input/output format classes for Sky Batch.

Provides format classes aligned with Ray Data's API naming:

- ``JsonReader``  -> ``read_json``
- ``JsonWriter`` -> ``write_json``
- ``ImageWriter`` -> ``write_images(column=...)``

Each class is both a descriptor (path, to_dict/from_dict) and a handler
(__len__, download_batch, upload_batch, reduce_results).

"""
from abc import ABC
from abc import abstractmethod
import dataclasses
from dataclasses import dataclass
import hashlib
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
    Only readers registered server-side are accepted; custom formats must be
    installed and registered on the server, not shipped as source in a
    submitted task (see ``from_dict``).

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

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this reader to a dict.

        Auto-generated via ``dataclasses.asdict``: every field with a
        non-None value is included.  Subclasses normally do **not**
        need to override this.
        """
        d = {k: v for k, v in dataclasses.asdict(self).items() if v is not None}
        d['format'] = self._format_name()
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> 'InputReader':
        """Reconstruct an InputReader from a dict.

        Only formats registered server-side in ``INPUT_READER_REGISTRY`` are
        accepted. The dict originates from a client-submitted task and is
        reconstructed on the shared jobs controller, so loading a format class
        from source embedded in the dict would be remote code execution.
        Custom formats must be installed and registered on the server.
        """
        # from_str resolves aliases and case and raises ValueError for an
        # unknown name; it returns None only when no format was given.
        cls = registry.INPUT_READER_REGISTRY.from_str(d.get('format'))
        if cls is None:
            raise ValueError('Input reader dict has no `format` key.')
        return cls.from_dict_args(d)

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
    Only writers registered server-side are accepted; custom formats must be
    installed and registered on the server, not shipped as source in a
    submitted task (see ``from_dict``).

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

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this writer to a dict.

        Auto-generated via ``dataclasses.asdict``: every field with a
        non-None value is included.  Subclasses normally do **not**
        need to override this.
        """
        d = {k: v for k, v in dataclasses.asdict(self).items() if v is not None}
        d['format'] = self._format_name()
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> 'OutputWriter':
        """Reconstruct an OutputWriter from a dict.

        Only formats registered server-side in ``OUTPUT_WRITER_REGISTRY`` are
        accepted. The dict originates from a client-submitted task and is
        reconstructed on the shared jobs controller, so loading a format class
        from source embedded in the dict would be remote code execution.
        Custom formats must be installed and registered on the server.
        """
        # from_str resolves aliases and case and raises ValueError for an
        # unknown name; it returns None only when no format was given.
        cls = registry.OUTPUT_WRITER_REGISTRY.from_str(d.get('format'))
        if cls is None:
            raise ValueError('Output writer dict has no `format` key.')
        return cls.from_dict_args(d)

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
class JsonReader(InputReader):
    """JSONL input reader.

    Corresponds to Ray Data's ``read_json``.

    Args:
        path: Cloud storage path to a ``.jsonl`` file.
              Supported prefixes: ``s3://``, ``gs://``.
    """

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError('JsonReader path cannot be empty')
        supported_prefixes = ('s3://', 'gs://')
        if not self.path.startswith(supported_prefixes):
            raise ValueError(
                f'Unsupported storage path: {self.path}. '
                f'Supported prefixes: {", ".join(supported_prefixes)}')
        if not self.path.endswith('.jsonl'):
            raise ValueError(
                f'JsonReader path must end with .jsonl: {self.path}')

    def __len__(self) -> int:
        # S3/GCS APIs do not expose line counts, so we must download
        # the file to count items.
        return utils.count_jsonl_lines_from_cloud(self.path)

    def download_batch(self, start_idx: int, end_idx: int,
                       cache_dir: str) -> List[Dict[str, Any]]:
        path_hash = hashlib.md5(self.path.encode(),
                                usedforsecurity=False).hexdigest()
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
class JsonWriter(OutputWriter):
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
            raise ValueError('JsonWriter path cannot be empty')
        supported_prefixes = ('s3://', 'gs://')
        if not self.path.startswith(supported_prefixes):
            raise ValueError(
                f'Unsupported storage path: {self.path}. '
                f'Supported prefixes: {", ".join(supported_prefixes)}')
        if not self.path.endswith('.jsonl'):
            raise ValueError(
                f'JsonWriter path must end with .jsonl: {self.path}')

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
class ImageWriter(OutputWriter):
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
            raise ValueError('ImageWriter path cannot be empty')
        supported_prefixes = ('s3://', 'gs://')
        if not self.path.startswith(supported_prefixes):
            raise ValueError(
                f'Unsupported storage path: {self.path}. '
                f'Supported prefixes: {", ".join(supported_prefixes)}')
        if not self.path.endswith('/'):
            raise ValueError(f'ImageWriter path must end with /: {self.path}')

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
