"""Image directory output format for Sky Batch.

This module implements a DatasetFormat for saving image outputs.
Input is still JSONL (handled by JSONLDataset), but output is a directory
of image files.

Strategy:
- upload_chunk(): Save each result's image column (PIL Image) as a PNG file
  directly to the output directory.
- merge_results(): No-op — images are already in their final location.

Output structure:
    {output_dir}/
        00000000.png
        00000001.png
        ...
"""
import io
import logging
from typing import Any, Dict, List

from sky.batch import utils
from sky.batch.dataset_base import DatasetFormat

logger = logging.getLogger(__name__)


class ImageDirOutput(DatasetFormat):
    """Image directory output format.

    Saves each result's image column as a separate PNG file in cloud
    storage.

    Args:
        column: Name of the key in result dicts that holds the PIL Image.
                Defaults to ``'image'``.
    """

    def __init__(self, column: str = 'image'):
        self.column = column

    # ------------------------------------------------------------------
    # Input methods (not applicable for output-only format)
    # ------------------------------------------------------------------

    def count_items(self, dataset_path: str) -> int:
        raise NotImplementedError('ImageDirOutput is an output-only format. '
                                  'Use JSONLDataset for input.')

    def get_metadata(self, dataset_path: str) -> Dict[str, Any]:
        raise NotImplementedError('ImageDirOutput is an output-only format.')

    def download_chunk(self, dataset_path: str, start_idx: int, end_idx: int,
                       cache_dir: str) -> List[Dict[str, Any]]:
        raise NotImplementedError('ImageDirOutput is an output-only format.')

    # ------------------------------------------------------------------
    # Output methods
    # ------------------------------------------------------------------

    def upload_chunk(self, results: List[Dict[str, Any]], output_path: str,
                     batch_idx: int, start_idx: int, end_idx: int,
                     job_id: str) -> str:
        """Save images as individual PNG files in the output directory.

        For each result dict, extracts the PIL Image from ``self.column``
        and uploads it as ``{output_dir}/{global_idx:08d}.png``.

        Args:
            results: List of result dicts.  The key named ``self.column``
                     must hold a PIL Image (with a ``.save()`` method).
            output_path: Output directory path (e.g.
                         ``s3://bucket/images/``).
            batch_idx: Batch index.
            start_idx: Global starting index in the dataset.
            end_idx: Global ending index (inclusive).
            job_id: Job ID for namespacing temp files.

        Returns:
            The output directory path (images are in their final location).
        """
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
