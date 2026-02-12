"""JSONL format handler for Sky Batch.

This module implements the DatasetFormat interface for JSONL files.

Strategy:
- count_items(): Download file and count lines (unavoidable for JSONL)
- download_chunk(): Download full file to cache, then slice by line range
- upload_chunk(): Upload results as JSONL chunk file
- merge_results(): Use existing concatenation logic
"""
import hashlib
import json
import os
from typing import Any, Dict, List

from sky.batch import utils
from sky.batch.dataset_base import DatasetFormat


class JSONLDataset(DatasetFormat):
    """JSONL format handler - line-based chunking."""

    def count_items(self, dataset_path: str) -> int:
        """Count lines in JSONL file.

        Strategy: Download file and count lines. This is unavoidable for
        JSONL format since cloud APIs don't provide line counts. However,
        this only happens once on the controller to determine batch count.

        Workers will also download once but for actual processing.
        """
        # Download and count lines
        data = utils.load_jsonl_from_cloud(dataset_path)
        return len(data)

    def get_metadata(self, dataset_path: str) -> Dict[str, Any]:
        """Get metadata including total item count."""
        total_items = self.count_items(dataset_path)
        return {
            'total_items': total_items,
            'format': 'jsonl',
            'path': dataset_path,
        }

    def download_chunk(self, dataset_path: str, start_idx: int, end_idx: int,
                       cache_dir: str) -> List[Dict[str, Any]]:
        """Download JSONL file (with caching) and slice lines.

        Strategy:
        1. Check if file is already cached
        2. If not, download full file to cache_dir
        3. Load all lines (memory efficient for most batch datasets)
        4. Return lines[start_idx:end_idx+1]
        """
        # Generate cache path
        cache_filename = self._get_cache_filename(dataset_path)
        cache_path = os.path.join(cache_dir, cache_filename)

        # Download if not cached
        if not os.path.exists(cache_path):
            os.makedirs(cache_dir, exist_ok=True)
            full_data = utils.load_jsonl_from_cloud(dataset_path)
            # Write to cache as JSONL
            with open(cache_path, 'w', encoding='utf-8') as f:
                for item in full_data:
                    f.write(json.dumps(item) + '\n')

        # Load from cache and slice
        data = []
        with open(cache_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                if i >= start_idx and i <= end_idx:
                    data.append(json.loads(line.strip()))
                elif i > end_idx:
                    break

        return data

    def upload_chunk(self, results: List[Dict[str, Any]], output_path: str,
                     batch_idx: int, start_idx: int, end_idx: int,
                     job_id: str) -> str:
        """Upload results as JSONL chunk file.

        Args:
            results: List of result items
            output_path: Base output path
            batch_idx: Batch index (for logging/debugging)
            start_idx: Starting index in the original dataset
            end_idx: Ending index in the original dataset
            job_id: Job ID for namespacing

        Returns:
            Cloud path to uploaded chunk
        """
        # Use existing utils to generate chunk path
        chunk_path = utils.get_chunk_path(output_path, start_idx, end_idx,
                                          job_id)

        utils.save_jsonl_to_cloud(results, chunk_path)
        return chunk_path

    def merge_results(self, output_path: str, job_id: str) -> None:
        """Concatenate JSONL chunks into final output."""
        # Use existing concatenate_chunks_to_output which handles:
        # 1. Listing chunk files
        # 2. Downloading and concatenating
        # 3. Uploading final result
        # 4. Cleaning up temp files
        utils.concatenate_chunks_to_output(output_path, job_id)

    def _get_cache_filename(self, dataset_path: str) -> str:
        """Generate stable cache filename from dataset path."""
        path_hash = hashlib.md5(dataset_path.encode()).hexdigest()
        return f'dataset_{path_hash}.jsonl'
