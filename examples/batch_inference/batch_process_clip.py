import asyncio
import logging
import pickle
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import numpy as np
from processors import BatchInferenceProcessor
from processors import HuggingFaceDatasetMixin
import torch

class ClipBatchProcessor(HuggingFaceDatasetMixin,
                         BatchInferenceProcessor[Dict[str, Any], Tuple[str, bytes]]):
    """Example implementation for processing images with CLIP."""

    def __init__(
            self,
            model_name: str = "ViT-bigG-14",
            dataset_name: str = "laion/relaion2B-en-research-safe",
            max_preprocessing_tasks: int = 10,  # Control parallel preprocessing
            **kwargs):
        super().__init__(model_name=model_name,
                         dataset_name=dataset_name,
                         **kwargs)
        self.preprocess = None
        self.preprocessing_semaphore = asyncio.Semaphore(
            max_preprocessing_tasks)

    async def setup_model(self):
        """Set up the CLIP model."""
        import open_clip

        model, _, preprocess = open_clip.create_model_and_transforms(
            self.model_name,
            pretrained="laion2b_s39b_b160k",
            device=self.device)
        self.model = model
        self.preprocess = preprocess

    async def run_model_inference(
            self, model_inputs: List[Tuple[str, torch.Tensor]]) -> List[Tuple[str, bytes]]:
        """Run CLIP model on a batch of preprocessed images."""
        # Unzip the URLs and tensors
        urls, tensors = zip(*model_inputs)
        
        # Stack inputs into a batch
        batch_tensor = torch.stack(tensors).to(self.device)

        # Run inference
        with torch.no_grad():
            features = self.model.encode_image(batch_tensor)
            features /= features.norm(dim=-1, keepdim=True)

        # Convert to numpy arrays, then to bytes, and pair with URLs
        embeddings = features.cpu().numpy()
        embedding_bytes = [pickle.dumps((url, arr)) for url, arr in zip(urls, embeddings)]
        return embedding_bytes

    async def do_data_loading(self) -> AsyncIterator[Tuple[int, Tuple[str, torch.Tensor]]]:
        """Load and preprocess data in parallel."""
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
                    if result[1][1] is not None:  # Check tensor in (idx, (url, tensor))
                        yield result

            # Start new preprocessing task
            async def preprocess_with_index(idx, item):
                async with self.preprocessing_semaphore:
                    url = item["url"]
                    tensor = await self._preprocess_input(item)
                    return (idx, (url, tensor))  # Return tuple with url

            task = asyncio.create_task(preprocess_with_index(idx, item))
            preprocessing_tasks.append(task)

        # Wait for and yield remaining results
        if preprocessing_tasks:
            done = await asyncio.gather(*preprocessing_tasks)
            for result in done:
                if result[1][1] is not None:  # Check tensor in (idx, (url, tensor))
                    yield result

    async def _preprocess_input(self,
                                item: Dict[str, Any]) -> Optional[torch.Tensor]:
        """Download and preprocess a single image."""
        from io import BytesIO

        import aiohttp
        from PIL import Image

        url = item["url"]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10, ssl=False) as response:
                    if response.status == 200:
                        data = await response.read()
                        img = Image.open(BytesIO(data))
                        return self.preprocess(img)
        except Exception as e:
            logging.debug(f"Error preprocessing image from {url}: {str(e)}")
        return None


async def main():
    """Example usage of the batch processing framework."""
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Run CLIP batch processing')
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
                        default=1000,
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
    parser.add_argument('--dataset-name',
                        type=str,
                        default='laion/relaion2B-en-research-safe',
                        help='HuggingFace dataset name')

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Initialize processor
    processor = ClipBatchProcessor(output_path=args.output_path,
                                   start_idx=args.start_idx,
                                   end_idx=args.end_idx,
                                   batch_size=args.batch_size,
                                   checkpoint_size=args.checkpoint_size,
                                   model_name=args.model_name,
                                   dataset_name=args.dataset_name)

    # Run processing
    await processor.run()


if __name__ == "__main__":
    asyncio.run(main())
