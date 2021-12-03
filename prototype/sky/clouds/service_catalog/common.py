"""Common utilities for service_catalog."""
import os

import pandas as pd


def get_data_path(filename: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data',
                        filename)


def read_catalog(filename: str) -> pd.DataFrame:
    return pd.read_csv(get_data_path(filename))
