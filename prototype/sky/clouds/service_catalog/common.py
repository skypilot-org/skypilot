import os

import pandas as pd


def read_catalog(filename: str) -> pd.DataFrame:
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data',
                             filename)
    return pd.read_csv(data_path)
