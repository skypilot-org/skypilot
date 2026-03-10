"""Typed input/output format descriptors for Sky Batch.

Provides lightweight descriptor classes aligned with Ray Data's API naming:

- ``JsonInput``  -> ``read_json``
- ``JsonOutput`` -> ``write_json``
- ``ImageOutput`` -> ``write_images(column=...)``

Each descriptor carries configuration (path, column, etc.) and can produce
the underlying ``DatasetFormat`` handler via ``get_handler()``.
"""
from typing import Any, Dict


class InputFormat:
    """Base class for input format descriptors."""

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

    def get_handler(self):
        """Return the DatasetFormat implementation for this input."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(path={self.path!r})'


class OutputFormat:
    """Base class for output format descriptors."""

    def __init__(self, path: str):
        self.path = path

    def to_dict(self) -> Dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> 'OutputFormat':
        """Reconstruct an OutputFormat from a dict."""
        fmt = d.get('format')
        if fmt == 'json':
            return JsonOutput(d['path'])
        elif fmt == 'image':
            return ImageOutput(d['path'], column=d.get('column', 'image'))
        raise ValueError(f'Unknown output format: {fmt}')

    def get_handler(self):
        """Return the DatasetFormat implementation for this output."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(path={self.path!r})'


# ---- Concrete input formats ------------------------------------------------


class JsonInput(InputFormat):
    """JSONL input format descriptor.

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

    def get_handler(self):
        from sky.batch.formats.jsonl import (
            JSONLDataset)  # pylint: disable=import-outside-toplevel
        return JSONLDataset()


# ---- Concrete output formats -----------------------------------------------


class JsonOutput(OutputFormat):
    """JSONL output format descriptor.

    Corresponds to Ray Data's ``write_json``.

    Args:
        path: Cloud storage path for the output ``.jsonl`` file.
              Supported prefixes: ``s3://``, ``gs://``, ``r2://``.
    """

    def __init__(self, path: str):
        super().__init__(path)
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
        return {'format': 'json', 'path': self.path}

    def get_handler(self):
        from sky.batch.formats.jsonl import (
            JSONLDataset)  # pylint: disable=import-outside-toplevel
        return JSONLDataset()


class ImageOutput(OutputFormat):
    """Image directory output format descriptor.

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

    def get_handler(self):
        from sky.batch.formats.image_dir import (
            ImageDirOutput)  # pylint: disable=import-outside-toplevel
        return ImageDirOutput(column=self.column)

    def __repr__(self) -> str:
        return (f'ImageOutput(path={self.path!r}, '
                f'column={self.column!r})')
