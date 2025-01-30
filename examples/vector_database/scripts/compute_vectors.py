import abc
import asyncio
import base64
from io import BytesIO
import logging
import os
from pathlib import Path
import pickle
import shutil
from typing import (Any, AsyncIterator, Dict, Generic, List, Optional, Tuple,
                    TypeVar)

import numpy as np
import pandas as pd
from PIL import Image
import pyarrow.parquet as pq
import torch
from tqdm import tqdm

InputType = TypeVar('InputType')
OutputType = TypeVar('OutputType')
ModelInputType = TypeVar('ModelInputType')
ModelOutputType = TypeVar('ModelOutputType')


class BatchProcessor():
    """Process ImageNet images with CLIP."""

    def __init__(self,
                 output_path: str,
                 model_name: str = "ViT-bigG-14",
                 dataset_name: str = "ILSVRC/imagenet-1k",
                 pretrained: str = "laion2b_s39b_b160k",
                 device: Optional[str] = None,
                 split: str = "train",
                 streaming: bool = True,
                 batch_size: int = 32,
                 checkpoint_size: int = 100,
                 start_idx: int = 0,
                 end_idx: Optional[int] = None):
        self.output_path = Path(output_path)  # Convert to Path object
        self.batch_size = batch_size
        self.checkpoint_size = checkpoint_size
        self.start_idx = start_idx
        self.end_idx = end_idx
        self._current_batch = []

        # CLIP-specific attributes
        self.model_name = model_name
        self.pretrained = pretrained
        self.device = device or ('cuda' if torch.cuda.is_available() else 'cpu')
        self.dataset_name = dataset_name
        self.split = split
        self.streaming = streaming
        self.model = None
        self.preprocess = None
        self.partition_counter = 0

    async def setup_model(self):
        """Set up the CLIP model."""
        import open_clip

        model, _, preprocess = open_clip.create_model_and_transforms(
            self.model_name, pretrained=self.pretrained, device=self.device)
        self.model = model
        self.preprocess = preprocess

    async def get_dataset_iterator(self) -> AsyncIterator[Tuple[int, Any]]:
        """Load data from a HuggingFace dataset."""
        from datasets import load_dataset

        dataset = load_dataset(self.dataset_name,
                               streaming=self.streaming,
                               trust_remote_code=True)[self.split]

        if self.start_idx > 0:
            dataset = dataset.skip(self.start_idx)

        for idx, item in enumerate(dataset, start=self.start_idx):
            if self.end_idx and idx >= self.end_idx:
                break
            yield idx, item

    async def do_data_loading(
            self) -> AsyncIterator[Tuple[int, Tuple[torch.Tensor, Any]]]:
        """Load and preprocess ImageNet images."""
        if self.model is None:
            await self.setup_model()

        async for idx, item in self.get_dataset_iterator():
            try:
                # ImageNet provides PIL Images directly
                tensor = self.preprocess(item['image'])
                if tensor is not None:
                    # Pass through both the tensor and original image
                    yield idx, (tensor, item['image'])
            except Exception as e:
                logging.debug(
                    f"Error preprocessing image at index {idx}: {str(e)}")

    async def do_batch_processing(
        self, batch: List[Tuple[int, Tuple[torch.Tensor, Any]]]
    ) -> List[Tuple[int, bytes]]:
        """Process a batch of images through CLIP."""
        if self.model is None:
            await self.setup_model()

        # Unpack the batch
        indices, batch_data = zip(*batch)
        model_inputs, original_images = zip(*batch_data)

        # Stack inputs into a batch
        batch_tensor = torch.stack(model_inputs).to(self.device)

        # Run inference
        with torch.no_grad():
            features = self.model.encode_image(batch_tensor)
            features /= features.norm(dim=-1, keepdim=True)

        # Convert to numpy arrays
        embeddings = features.cpu().numpy()

        # Convert original images to base64
        images_base64 = {}
        for idx, img in zip(indices, original_images):
            buffered = BytesIO()
            img.save(buffered, format="JPEG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            images_base64[idx] = img_str

        # Return both embeddings and images
        return [(idx, pickle.dumps((images_base64[idx], arr)))
                for idx, arr in zip(indices, embeddings)]

    async def find_existing_progress(self) -> Tuple[int, int]:
        """
        Find the highest processed index and partition counter from existing files.
        Returns:
            Tuple[int, int]: (highest_index, next_partition_number)
        """
        if not self.output_path.parent.exists():
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            return self.start_idx, 0

        partition_files = list(
            self.output_path.parent.glob(
                f"{self.output_path.stem}_part_*.parquet"))
        print(f"Partition files: {partition_files}")
        if not partition_files:
            return self.start_idx, 0

        max_idx = self.start_idx
        max_partition = -1

        for file in partition_files:
            # Extract partition number from filename
            try:
                partition_num = int(file.stem.split('_part_')[1])
                max_partition = max(max_partition, partition_num)

                # Read the file and find highest index
                df = pd.read_parquet(file)
                if not df.empty:
                    max_idx = max(max_idx, df['idx'].max())
            except Exception as e:
                logging.warning(f"Error processing file {file}: {e}")

        return max_idx, max_partition + 1

    async def run(self):
        """
        Run the batch processing pipeline with recovery support.
        """
        # Find existing progress
        resume_idx, self.partition_counter = await self.find_existing_progress()
        self.start_idx = max(self.start_idx, resume_idx + 1)

        logging.info(
            f"Starting processing from index {self.start_idx} (partition {self.partition_counter})"
        )

        results = []

        def save_results_to_parquet(self, results: list):
            """Save results to a parquet file with atomic write."""
            if not results:
                return

            df = pd.DataFrame(results, columns=["idx", "output"])
            final_path = f"{self.output_path}_part_{self.partition_counter}.parquet"
            temp_path = f"/tmp/{self.partition_counter}.tmp"

            # Write to temporary file first
            df.to_parquet(temp_path, engine="pyarrow", index=False)

            # Copy from temp to final destination
            shutil.copy2(temp_path, final_path)
            os.remove(temp_path)  # Clean up temp file

            logging.info(
                f"Saved partition {self.partition_counter} to {final_path} with {len(df)} rows"
            )
            self.partition_counter += 1

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

        # Process any remaining items in the batch
        if self._current_batch:
            batch_results = await self.do_batch_processing(self._current_batch)
            results.extend(batch_results)

        # Write the final partition if there are any leftover results
        if results:
            self.save_results_to_parquet(results)


async def main():
    """Example usage of the batch processing framework."""
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Run CLIP batch processing on ImageNet')
    parser.add_argument('--output-path',
                        type=str,
                        default='embeddings.parquet',
                        help='Path to output parquet file')
    parser.add_argument('--start-idx',
                        type=int,
                        default=0,
                        help='Starting index in dataset')
    parser.add_argument('--end-idx',
                        type=int,
                        default=10000,
                        help='Ending index in dataset')
    parser.add_argument('--batch-size',
                        type=int,
                        default=50,
                        help='Batch size for processing')
    parser.add_argument('--checkpoint-size',
                        type=int,
                        default=100,
                        help='Number of results before checkpointing')
    parser.add_argument('--model-name',
                        type=str,
                        default='ViT-bigG-14',
                        help='CLIP model name')

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Initialize processor
    processor = BatchProcessor(output_path=args.output_path,
                               start_idx=args.start_idx,
                               end_idx=args.end_idx,
                               batch_size=args.batch_size,
                               checkpoint_size=args.checkpoint_size,
                               model_name=args.model_name)

    # Run processing
    await processor.run()


if __name__ == "__main__":
    asyncio.run(main())
