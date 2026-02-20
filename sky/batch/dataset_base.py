"""Abstract base class for dataset format handlers.

This module defines the interface for dataset format handlers in Sky Batch.
Each format (JSONL, files, etc.) implements this interface to provide
format-specific logic for chunking, download, upload, and merging.

The key design principle is to keep the controller out of the data path:
- Controller only calls count_items() to determine batch count
- Workers download chunks directly from source dataset
- Workers upload results directly to cloud storage
- Merging happens via cloud-side operations when possible
"""
from abc import ABC
from abc import abstractmethod
from typing import Any, Dict, List


class DatasetFormat(ABC):
    """Abstract base class for dataset format handlers.

    Each format (JSONL, Files, etc.) implements this interface to provide
    format-specific logic for chunking, download, upload, and merging.
    """

    @abstractmethod
    def count_items(self, dataset_path: str) -> int:
        """Count total items in the dataset efficiently.

        This should be as efficient as possible - ideally without downloading
        the full dataset. For example:
        - JSONL: Download and count lines (cache the count if needed)
        - File list: Read manifest file and count entries
        - Large files: Use streaming line count

        Args:
            dataset_path: Path to the dataset

        Returns:
            Total number of items in the dataset
        """

    @abstractmethod
    def get_metadata(self, dataset_path: str) -> Dict[str, Any]:
        """Get dataset metadata without downloading data.

        Returns:
            Dictionary with at least:
                'total_items': int, # Total number of items (from count_items())
                'format': str,      # 'jsonl', 'files', etc.
                'path': str,        # Original dataset path
        """

    @abstractmethod
    def download_chunk(self, dataset_path: str, start_idx: int, end_idx: int,
                       cache_dir: str) -> List[Dict[str, Any]]:
        """Download data for a specific chunk range.

        Args:
            dataset_path: Original dataset path (e.g., s3://bucket/data.jsonl)
            start_idx: Starting index (inclusive)
            end_idx: Ending index (inclusive)
            cache_dir: Local cache directory for downloaded files

        Returns:
            List of data items for this chunk
        """

    @abstractmethod
    def upload_chunk(self, results: List[Dict[str, Any]], output_path: str,
                     batch_idx: int, start_idx: int, end_idx: int,
                     job_id: str) -> str:
        """Upload results for a specific chunk.

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

    @abstractmethod
    def merge_results(self, output_path: str, job_id: str) -> None:
        """Merge all result chunks into final output.

        This should be a cloud-side operation when possible (no downloads).

        Args:
            output_path: Final output path
            job_id: Job ID for finding temp chunks
        """
