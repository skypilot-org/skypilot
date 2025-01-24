import abc
import asyncio
from asyncio import Queue
from asyncio import Semaphore
import logging
from pathlib import Path
from typing import (Any, AsyncIterator, Dict, Generic, List, Optional, Tuple,
                    TypeVar)

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

# Generic type variables for input and output data
InputType = TypeVar('InputType')
OutputType = TypeVar('OutputType')


class BatchProcessor(Generic[InputType, OutputType], abc.ABC):
    """Abstract base class for batch processing with async queues and checkpointing.
    
    This class provides a framework for:
    1. Loading data from an iterator (e.g. HuggingFace datasets)
    2. Processing data in batches with user-defined logic
    3. Automatic checkpointing to parquet files
    """

    def __init__(
        self,
        output_path: str,
        start_idx: int = 0,
        end_idx: Optional[int] = None,
        batch_size: int = 50,
        checkpoint_size: int = 1000,
        max_concurrent_tasks: int = 50,
    ):
        """Initialize the batch processor.
        
        Args:
            output_path: Path to save the output parquet file
            start_idx: Starting index in the dataset
            end_idx: Ending index in the dataset (if None, process all data)
            batch_size: Number of items to process in each batch
            checkpoint_size: Number of results to accumulate before checkpointing
            max_concurrent_tasks: Maximum number of concurrent tasks
        """
        self.output_path = Path(output_path)
        self.output_path.mkdir(parents=True, exist_ok=True)  # Create directory if it doesn't exist
        self.start_idx = start_idx
        self.end_idx = end_idx
        self.batch_size = batch_size
        self.checkpoint_size = checkpoint_size
        self.semaphore = Semaphore(max_concurrent_tasks)

        # Initialize queues
        self.load_queue: Queue = Queue(maxsize=500)
        self.process_queue: Queue = Queue(maxsize=500)
        self.write_queue: Queue = Queue(maxsize=500)

        # Initialize state
        self._setup_output_file()

        # Calculate total items to process
        self.total_items = self.end_idx - self.start_idx if self.end_idx else None

        logging.info(
            f"start_idx: {self.start_idx}, end_idx: {self.end_idx}, total_items: {self.total_items}"
        )
        if self.total_items < 0:
            raise ValueError("Total items to process must be greater than 0")

        # Initialize progress bars
        self.load_pbar = tqdm(total=self.total_items,
                              desc="Loading",
                              position=0)
        self.process_pbar = tqdm(total=self.total_items,
                                 desc="Processing",
                                 position=1)
        self.write_pbar = tqdm(total=self.total_items,
                               desc="Writing",
                               position=2)

    def __del__(self):
        """Clean up progress bars."""
        if hasattr(self, 'load_pbar'):
            self.load_pbar.close()
        if hasattr(self, 'process_pbar'):
            self.process_pbar.close()
        if hasattr(self, 'write_pbar'):
            self.write_pbar.close()

    def _setup_output_file(self):
        """Set up the output directory and determine start index."""
        if not self.output_path.exists():
            self.output_path.mkdir(parents=True)
        else:
            # Find the highest processed index from existing partitions
            partition_files = list(self.output_path.glob("*.parquet"))
            if partition_files:
                max_idx = 0
                for file in partition_files:
                    table = pq.read_table(file)
                    df = table.to_pandas()
                    if not df.empty:
                        max_idx = max(max_idx, df['idx'].max())
                self.start_idx = max(self.start_idx, max_idx + 1)
                logging.info(f"Resuming from index {self.start_idx}")

    @abc.abstractmethod
    async def do_data_loading(self) -> AsyncIterator[Tuple[int, InputType]]:
        """Load data from source and yield (index, input) tuples."""
        raise NotImplementedError

    @abc.abstractmethod
    async def do_batch_processing(
            self,
            batch: List[Tuple[int, InputType]]) -> List[Tuple[int, OutputType]]:
        """Process a batch of inputs and return corresponding outputs."""
        raise NotImplementedError

    async def _load_worker(self):
        """Worker to load data and put it in the process queue."""
        try:
            async for idx, input_data in self.do_data_loading():
                if self.end_idx and idx >= self.end_idx:
                    break
                if idx >= self.start_idx:
                    await self.load_queue.put((idx, input_data))
                    self.load_pbar.update(1)
        except asyncio.CancelledError:
            return
        finally:
            # Signal end of data
            await self.load_queue.put((None, None))

    async def _process_worker(self):
        """Worker to process batches from the process queue."""
        try:
            batch = []
            while True:
                try:
                    idx, input_data = await self.load_queue.get()
                    if idx is None:  # End of data
                        break

                    batch.append((idx, input_data))

                    if len(batch) >= self.batch_size:
                        results = await self.do_batch_processing(batch)
                        for result in results:
                            await self.write_queue.put(result)
                        self.process_pbar.update(len(results))
                        batch = []
                finally:
                    self.load_queue.task_done()

            # Process remaining items in batch
            if batch:
                results = await self.do_batch_processing(batch)
                for result in results:
                    await self.write_queue.put(result)
                self.process_pbar.update(len(results))

            # Signal end of processing
            await self.write_queue.put((None, None))
        except asyncio.CancelledError:
            return

    async def _write_worker(self):
        """Worker to write results to parquet file."""
        try:
            buffer = []
            while True:
                idx, output = await self.write_queue.get()
                if idx is None:  # End of data
                    break

                buffer.append({'idx': idx, 'output': output})
                self.write_pbar.update(1)

                if len(buffer) >= self.checkpoint_size:
                    await self._checkpoint_results(buffer)
                    buffer = []

            # Write remaining results
            if buffer:
                await self._checkpoint_results(buffer)
        except asyncio.CancelledError:
            if buffer:
                await self._checkpoint_results(buffer)
        finally:
            self.write_queue.task_done()

    async def _checkpoint_results(self, results: List[Dict[str, Any]]):
        """Checkpoint results to a new parquet partition."""
        # Create new dataframe with results
        new_df = pd.DataFrame(results)
        
        # Generate partition filename based on index range
        min_idx = new_df['idx'].min()
        max_idx = new_df['idx'].max()
        partition_file = self.output_path / f"part_{min_idx:08d}_{max_idx:08d}.parquet"
        
        # Write to new partition file
        table = pa.Table.from_pandas(new_df)
        pq.write_table(table, partition_file)

    async def run(self):
        """Run the batch processing pipeline."""
        try:
            # Start workers
            load_task = asyncio.create_task(self._load_worker())
            process_task = asyncio.create_task(self._process_worker())
            write_task = asyncio.create_task(self._write_worker())

            # Wait for all tasks to complete
            await asyncio.gather(load_task, process_task, write_task)

        finally:
            # Clean up progress bars
            self.load_pbar.close()
            self.process_pbar.close()
            self.write_pbar.close()
