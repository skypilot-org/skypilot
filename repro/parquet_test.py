# Creates a random pandas dataframe and writes it to a parquet file
# and verifies that it can be read back in.
import argparse
import pandas as pd
import numpy as np


def test_parquet(path: str, size: int = 100):
    df = pd.DataFrame(np.random.randint(0, 100, size=(size, size)), columns=[f'col_{i}' for i in range(size)])
    df.to_parquet(path)
    df2 = pd.read_parquet(path)
    assert df.equals(df2)


if __name__ == '__main__':
    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', type=str, default='test.parquet')
    parser.add_argument('--size', type=int, default=100)
    args = parser.parse_args()
    # Run till test_parquet fails
    i = 0
    while True:
        try:
            test_parquet(args.path, args.size)
            i += 1
            print(f'Passed {i} times')
        except AssertionError as e:
            print(e)
            break
