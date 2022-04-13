# Read all files in a directory recursively in parallel

import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed


def parse_args():
    import argparse
    parser = argparse.ArgumentParser(description='Recursively read in parallel')
    parser.add_argument('path', help='Path to directory to read files;')
    parser.add_argument('--list', action='store_true', help='List files before reading')
    args = parser.parse_args()
    return args


def sizeof_fmt(num, suffix="B"):
    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def read_file(file):
    print(file)
    with open(file, 'rb') as f:
        x = f.read()
        # print("File: " + file)
        # print("Size of file is :", sizeof_fmt(int(f.tell())))


def list_files(path):
    # Returns the number of ls ops done and files ls'd
    ls_done = 0
    files_lsed = 0
    for root, dirs, files in os.walk(path):
        files = [os.path.join(root, file) for file in files]
        ls_done += 1
        files_lsed += len(files)
        if ls_done % 1000 == 0:
            print("ls done: ", ls_done)
            print("files ls'd: ", files_lsed)
    print("Got files: ", len(files))
    return ls_done, files_lsed


def read_parallel(path):
    print(f"Reading files in parallel {path}")
    done = 0
    for root, dirs, files in os.walk(path):  # Uses a generator
        with ThreadPoolExecutor(max_workers=64) as executor:
            futures = [executor.submit(read_file, os.path.join(root, file)) for file in files]
            for future in as_completed(futures):
                done += 1
                if done % 10 == 0:
                    print("Reads done: ", done)
                print(future.result())


if __name__ == '__main__':
    args = parse_args()
    # s3://fah-public-data-covid19-cryptic-pockets mounted at /covid
    if args.list:
        ls_done, files_lsed = list_files(args.path)
        print("ls done: ", ls_done)
        print("files ls'd: ", files_lsed)
    else:
        read_parallel(args.path)
