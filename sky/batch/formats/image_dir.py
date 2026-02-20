"""Image directory output format for Sky Batch.

This module implements a DatasetFormat for saving image outputs.
Input is still JSONL (handled by JSONLDataset), but output is a directory
of image files plus a manifest.jsonl.

Strategy:
- upload_chunk(): Save each result's 'image' field (PIL Image) as a PNG file
  directly to the output directory, then save metadata as a JSONL chunk.
- merge_results(): Concatenate metadata chunks into manifest.jsonl.
  Images are already in their final location (no copy needed).

Output structure:
    {output_dir}/
        00000000.png
        00000001.png
        ...
        manifest.jsonl   (metadata with image filenames)
"""
import io
import logging
from typing import Any, Dict, List

from sky.batch import constants
from sky.batch import utils
from sky.batch.dataset_base import DatasetFormat

logger = logging.getLogger(__name__)


class ImageDirOutput(DatasetFormat):
    """Image directory output format.

    Saves each result's 'image' field as a separate PNG file in cloud
    storage, and produces a manifest.jsonl with metadata for each image.
    """

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
        """Save images as individual files and metadata as a JSONL chunk.

        For each result dict:
        1. If it contains an ``image`` key holding a PIL Image, save it as
           ``{output_dir}/{global_idx:08d}.png`` and replace the value with
           the filename string.
        2. Collect the (now JSON-safe) metadata dicts.
        3. Write metadata as a JSONL chunk in the temp directory for later
           merging into ``manifest.jsonl``.

        Args:
            results: List of result dicts.  Any key whose value is a PIL
                     Image (has a ``.save()`` method) will be uploaded as
                     PNG.
            output_path: Output directory path (e.g.
                         ``s3://bucket/images/``).
            batch_idx: Batch index.
            start_idx: Global starting index in the dataset.
            end_idx: Global ending index (inclusive).
            job_id: Job ID for namespacing temp files.

        Returns:
            Cloud path to the JSONL metadata chunk.
        """
        output_dir = output_path.rstrip('/')
        metadata_results: List[Dict[str, Any]] = []

        for i, result in enumerate(results):
            global_idx = start_idx + i
            result_copy = dict(result)

            # Upload any PIL Image values as PNG files.
            for key, value in result.items():
                if hasattr(value, 'save'):
                    image_filename = f'{global_idx:08d}.png'
                    image_cloud_path = f'{output_dir}/{image_filename}'

                    buf = io.BytesIO()
                    value.save(buf, format='PNG')
                    buf.seek(0)
                    utils.upload_bytes_to_cloud(buf.read(), image_cloud_path)

                    # Replace the PIL Image with the filename in metadata.
                    result_copy[key] = image_filename
                    logger.debug('Uploaded image %s', image_cloud_path)

            metadata_results.append(result_copy)

        # Save metadata chunk for later merge into manifest.jsonl.
        manifest_base = f'{output_dir}/{constants.IMAGE_DIR_MANIFEST_FILENAME}'
        chunk_path = utils.get_chunk_path(manifest_base, start_idx, end_idx,
                                          job_id)
        utils.save_jsonl_to_cloud(metadata_results, chunk_path)

        logger.info('Uploaded %d images and metadata chunk for batch %d',
                    len(results), batch_idx)
        return chunk_path

    def merge_results(self, output_path: str, job_id: str) -> None:
        """Merge metadata JSONL chunks into ``manifest.jsonl``.

        Images are already saved in their final location during
        ``upload_chunk()``, so only the metadata needs merging.
        """
        output_dir = output_path.rstrip('/')
        manifest_path = f'{output_dir}/{constants.IMAGE_DIR_MANIFEST_FILENAME}'
        utils.concatenate_chunks_to_output(manifest_path, job_id)
        logger.info('Manifest written to %s', manifest_path)
