from typing import Any, AsyncIterator, Tuple


class HuggingFaceDatasetMixin:
    """Mixin for processors that load data from HuggingFace datasets."""

    def __init__(self,
                 dataset_name: str,
                 split: str = "train",
                 streaming: bool = True,
                 **kwargs):
        self.dataset_name = dataset_name
        self.split = split
        self.streaming = streaming
        super().__init__(**kwargs)

    async def get_dataset_iterator(self) -> AsyncIterator[Tuple[int, Any]]:
        """Load data from a HuggingFace dataset."""
        from datasets import load_dataset

        dataset = load_dataset(self.dataset_name,
                               streaming=self.streaming)[self.split]

        if self.start_idx > 0:
            dataset = dataset.skip(self.start_idx)

        for idx, item in enumerate(dataset, start=self.start_idx):
            if self.end_idx and idx >= self.end_idx:
                break
            yield idx, item
