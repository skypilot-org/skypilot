from abc import ABC
from abc import abstractmethod
import asyncio
import json
import logging
import os
from pathlib import Path
import pickle
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import torch


class BaseVectorProcessor(ABC):
    """Base class for processing data and computing vector embeddings.
    
    This abstract class provides a common framework for computing embeddings
    for different types of data (images, text) and datasets.
    """

    def __init__(self,
                 output_path: str,
                 dataset_name: str,
                 split: str = 'train',
                 streaming: bool = True,
                 batch_size: int = 32,
                 checkpoint_size: int = 100,
                 start_idx: int = 0,
                 end_idx: Optional[int] = None,
                 max_preprocessing_tasks: int = 10):
        """Initialize the base vector processor.
        
        Args:
            output_path: Path to save the computed vectors
            dataset_name: Name of the dataset to process
            split: Dataset split to use
            streaming: Whether to stream the dataset
            batch_size: Size of batches for processing
            checkpoint_size: Number of items to process before saving
            start_idx: Starting index in the dataset
            end_idx: Ending index in the dataset
            max_preprocessing_tasks: Maximum number of concurrent preprocessing tasks
        """
        self.output_path = Path(output_path)  # Convert to Path object
        self.batch_size = batch_size
        self.checkpoint_size = checkpoint_size
        self.start_idx = start_idx
        self.end_idx = end_idx
        self._current_batch = []

        # Dataset attributes
        self.dataset_name = dataset_name
        self.split = split
        self.streaming = streaming

        # Model attributes
        self.model = None
        self.partition_counter = 0

        # Control parallel preprocessing
        self.preprocessing_semaphore = asyncio.Semaphore(
            max_preprocessing_tasks)

        # Progress tracking
        self.metrics_path = Path(output_path).parent / 'metrics'
        self.metrics_path.mkdir(exist_ok=True)
        self.worker_id = os.getenv('WORKER_ID', 'unknown')
        self.metrics_file = self.metrics_path / f'worker_{self.worker_id}.json'
        self.metrics_history_file = self.metrics_path / f'worker_{self.worker_id}_history.json'
        self.processed_count = 0
        self.failed_count = 0
        self.start_time = time.time()
        self.last_update_time = self.start_time
        self.session_id = f"{self.worker_id}_{int(self.start_time)}"

        # Load existing history if available
        self.metrics_history = self._load_metrics_history()

    @abstractmethod
    async def setup_model(self):
        """Set up the model for computing embeddings."""
        pass

    @abstractmethod
    async def get_dataset_iterator(self) -> AsyncIterator[Tuple[int, Any]]:
        """Get an iterator over the dataset."""
        pass

    @abstractmethod
    async def _preprocess_input(self, item: Any) -> Optional[Any]:
        """Preprocess a single input item."""
        pass

    @abstractmethod
    async def do_batch_processing(self,
                                  batch: List[Any]) -> List[Tuple[int, Any]]:
        """Process a batch of preprocessed inputs."""
        pass

    @abstractmethod
    def save_results_to_parquet(self, results: List):
        """Save results to parquet file."""
        pass

    async def do_data_loading(self) -> AsyncIterator[Tuple[int, Any]]:
        """Load and preprocess inputs in parallel."""
        if self.model is None:
            await self.setup_model()

        preprocessing_tasks = []
        buffer_size = self.batch_size * 2

        async for idx, item in self.get_dataset_iterator():
            # Clean up completed tasks when buffer is full
            if len(preprocessing_tasks) >= buffer_size:
                done, pending = await asyncio.wait(
                    preprocessing_tasks, return_when=asyncio.FIRST_COMPLETED)
                preprocessing_tasks = list(pending)

                for task in done:
                    result = await task
                    if result is not None:
                        yield result

            # Start new preprocessing task
            async def preprocess_with_index(idx, item):
                async with self.preprocessing_semaphore:
                    processed = await self._preprocess_input(item)
                    if processed is not None:
                        return (idx, processed)
                    return None

            task = asyncio.create_task(preprocess_with_index(idx, item))
            preprocessing_tasks.append(task)

        # Wait for and yield remaining results
        if preprocessing_tasks:
            done = await asyncio.gather(*preprocessing_tasks)
            for result in done:
                if result is not None:
                    yield result

    def _load_metrics_history(self) -> List[Dict]:
        """Load metrics history from file."""
        if self.metrics_history_file.exists():
            try:
                with open(self.metrics_history_file, 'r') as f:
                    return eval(f.read())
            except Exception as e:
                logging.warning(f"Failed to load metrics history: {e}")
        return []

    def save_metrics_history(self):
        """Save metrics history to file."""
        try:
            with open(self.metrics_history_file, 'w') as f:
                json.dump(self.metrics_history, f)
        except Exception as e:
            logging.warning(f"Failed to save metrics history: {e}")

    def update_metrics(self):
        """Update processing metrics."""
        current_time = time.time()
        elapsed = current_time - self.start_time
        elapsed_since_update = current_time - self.last_update_time

        # Only compute throughput if some time has elapsed
        if elapsed_since_update > 0:
            processing_rate = self.processed_count / elapsed if elapsed > 0 else 0

            metrics = {
                'worker_id': self.worker_id,
                'session_id': self.session_id,
                'processed_count': self.processed_count,
                'failed_count': self.failed_count,
                'elapsed_seconds': elapsed,
                'items_per_second': processing_rate,
                'start_idx': self.start_idx,
                'current_idx': self.start_idx + self.processed_count,
                'end_idx': self.end_idx,
                'timestamp': current_time,
                'status': 'running',
                'partition_counter': self.partition_counter
            }

            # Add to history
            self.metrics_history.append(metrics)

            # Save to files
            try:
                with open(self.metrics_file, 'w') as f:
                    import json
                    json.dump(metrics, f)
                self.save_metrics_history()
            except Exception as e:
                logging.warning(f"Failed to save metrics: {e}")

        self.last_update_time = current_time

    async def find_existing_progress(self) -> Tuple[int, int]:
        """Find the latest progress from previous runs."""
        max_idx = self.start_idx - 1
        partition_counter = 0

        # Check metrics file first
        if self.metrics_file.exists():
            try:
                with open(self.metrics_file, 'r') as f:
                    import json
                    metrics = json.load(f)
                    if 'current_idx' in metrics:
                        max_idx = max(max_idx, metrics['current_idx'])
                    if 'partition_counter' in metrics:
                        partition_counter = metrics['partition_counter']
                    logging.info(
                        f"Recovered progress from metrics: idx={max_idx}, partition={partition_counter}"
                    )
            except Exception as e:
                logging.warning(f"Failed to recover from metrics file: {e}")

        # Check for existing parquet files as backup
        try:
            prefix = self.output_path.stem
            parent = self.output_path.parent
            existing_files = list(parent.glob(f"{prefix}*.parquet"))

            for file_path in existing_files:
                try:
                    df = pd.read_parquet(file_path)
                    if 'idx' in df.columns:
                        max_idx = max(max_idx, df['idx'].max())
                        logging.info(
                            f"Found existing progress in {file_path.name}: max_idx={max_idx}"
                        )

                    # Extract partition number from filename
                    import re
                    match = re.search(r'_part(\d+)\.parquet$', file_path.name)
                    if match:
                        part_num = int(match.group(1))
                        partition_counter = max(partition_counter, part_num + 1)
                except Exception as e:
                    logging.warning(
                        f"Failed to read parquet file {file_path}: {e}")
        except Exception as e:
            logging.warning(f"Failed to check for existing parquet files: {e}")

        return max_idx, partition_counter

    async def run(self):
        """Run the batch processing pipeline with recovery support."""
        try:
            # Initialize the model
            if self.model is None:
                await self.setup_model()

            # Find existing progress and recover state
            resume_idx, self.partition_counter = await self.find_existing_progress(
            )
            self.start_idx = max(self.start_idx, resume_idx + 1)

            logging.info(
                f'Starting processing from index {self.start_idx} (partition {self.partition_counter})'
            )

            # Record start event in history
            start_metrics = {
                'worker_id': self.worker_id,
                'session_id': self.session_id,
                'event': 'start',
                'start_idx': self.start_idx,
                'end_idx': self.end_idx,
                'timestamp': time.time(),
                'status': 'starting'
            }
            self.metrics_history.append(start_metrics)
            self.save_metrics_history()  # Explicitly save history for recovery
            self.update_metrics()  # Also save current state

            results = []

            async for idx, input_data in self.do_data_loading():
                self._current_batch.append((idx, input_data))
                if len(self._current_batch) >= self.batch_size:
                    batch_results = await self.do_batch_processing(
                        self._current_batch)
                    results.extend(batch_results)
                    self._current_batch = []

                    if len(results) >= self.checkpoint_size:
                        self.save_results_to_parquet(results)
                        results.clear()
                        # Save metrics history at each checkpoint for recovery
                        self.update_metrics()

            # Process any remaining items in the batch
            if self._current_batch:
                batch_results = await self.do_batch_processing(
                    self._current_batch)
                results.extend(batch_results)

            # Write the final partition if there are any leftover results
            if results:
                self.save_results_to_parquet(results)

            # Write final metrics
            end_metrics = {
                'worker_id': self.worker_id,
                'session_id': self.session_id,
                'event': 'end',
                'processed_count': self.processed_count,
                'failed_count': self.failed_count,
                'start_idx': self.start_idx,
                'end_idx': self.end_idx,
                'timestamp': time.time(),
                'status': 'completed'
            }
            self.metrics_history.append(end_metrics)
            self.update_metrics()
            self.save_metrics_history()

            logging.info(
                f'Completed processing {self.processed_count} items ({self.failed_count} failed)'
            )

        except Exception as e:
            logging.error(f"Error during batch processing: {e}", exc_info=True)
            # Record error in metrics
            error_metrics = {
                'worker_id': self.worker_id,
                'session_id': self.session_id,
                'event': 'error',
                'error': str(e),
                'timestamp': time.time(),
                'status': 'error'
            }
            self.metrics_history.append(error_metrics)
            self.save_metrics_history()
            raise
