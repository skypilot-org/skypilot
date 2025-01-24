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

