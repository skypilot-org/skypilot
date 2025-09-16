#!/usr/bin/env python3
"""Download and cache dataset and model with filesystem cache flushing.

This script downloads and caches the specified dataset and model, then flushes
the filesystem cache to ensure clean benchmarking conditions.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess

from datasets import load_dataset
from transformers import AutoModel
from transformers import AutoModelForImageTextToText


def flush_filesystem_cache(paths):
    """Use vmtouch to flush filesystem cache for given paths."""
    if not paths:
        return

    # Convert paths to strings if they're Path objects
    str_paths = [str(p) for p in paths]

    try:
        # First, try to evict the paths from cache
        cmd = ['vmtouch', '-e'] + str_paths
        print(f'Flushing filesystem cache for: {", ".join(str_paths)}')
        result = subprocess.run(cmd,
                                capture_output=True,
                                text=True,
                                timeout=300,
                                check=False)

        if result.returncode == 0:
            print('Successfully flushed filesystem cache')
            if result.stdout:
                print(f'vmtouch output: {result.stdout.strip()}')
        else:
            print(f'vmtouch failed with return code {result.returncode}')
            if result.stderr:
                print(f'vmtouch error: {result.stderr.strip()}')

    except subprocess.TimeoutExpired:
        print('vmtouch command timed out after 5 minutes')
    except FileNotFoundError:
        print('vmtouch not found. Please install vmtouch to enable cache '
              'flushing.')
        print('On Ubuntu/Debian: sudo apt-get install vmtouch')
        print('On CentOS/RHEL: sudo yum install vmtouch')
        print('On macOS: brew install vmtouch')
    except Exception as e:  # pylint: disable=broad-except
        print(f'Error running vmtouch: {e}')


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Download and cache dataset and model')
    parser.add_argument(
        '--model_id',
        type=str,
        default='google/gemma-3-12b-it',
        help='Model ID to download and cache (default: google/gemma-3-12b-it)')
    parser.add_argument(
        '--dataset_name',
        type=str,
        default='open-r1/codeforces-cots',
        help=
        'Dataset name to download and cache (default: open-r1/codeforces-cots)')
    parser.add_argument(
        '--dirs',
        nargs='*',
        default=['/tmp'],
        help='Directories to cache dataset and model to (e.g., --dirs /tmp '
        's3://my-bucket/cache)')
    parser.add_argument('--num_proc',
                        type=int,
                        default=None,
                        help='Processes used for data/model loading')
    parser.add_argument(
        '--clear_cache',
        action='store_true',
        default=False,
        help=
        'Clear existing cache directories before downloading (default: True)')
    parser.add_argument(
        '--no_clear_cache',
        dest='clear_cache',
        action='store_false',
        help='Don\'t clear existing cache directories before downloading')
    parser.add_argument(
        '--flush_cache',
        action='store_true',
        default=True,
        help='Flush filesystem cache after downloading (default: True)')
    parser.add_argument('--no_flush_cache',
                        dest='flush_cache',
                        action='store_false',
                        help='Don\'t flush filesystem cache after downloading')

    args = parser.parse_args()

    num_proc = args.num_proc
    if num_proc == -1:
        num_proc = os.cpu_count()

    # Determine the appropriate model class
    model_class = AutoModel
    if args.model_id == 'google/gemma-3-12b-it':
        model_class = AutoModelForImageTextToText

    # Separate S3 and non-S3 directories
    s3_dirs = []
    regular_dirs = []

    for dir_path in args.dirs:
        if 's3' in str(dir_path).lower():
            s3_dirs.append(dir_path)
        else:
            regular_dirs.append(dir_path)

    # Use /tmp/checkpoint as temp
    if s3_dirs and not regular_dirs:
        regular_dirs = ['/tmp/checkpoint']
        print('Adding /tmp/checkpoint as temporary directory for S3 operations')

    # Process regular (non-S3) directories first
    temp_cache_dir = None
    for i, base_path in enumerate(regular_dirs):
        print(f'\n=== Processing directory {i+1}/{len(regular_dirs)}: '
              f'{base_path} ===')

        # Convert base_path to Path object
        base_path = Path(base_path)

        # Set up cache directories
        dataset_cache_dir = base_path / 'dataset_cache'
        model_cache_dir = base_path / 'model_cache'
        checkpoint_saving_dir = base_path / 'checkpoints'

        if args.clear_cache:
            print('Clearing existing cache directories...')
            if dataset_cache_dir.exists():
                shutil.rmtree(dataset_cache_dir)
            if model_cache_dir.exists():
                shutil.rmtree(model_cache_dir)
            if checkpoint_saving_dir.exists():
                shutil.rmtree(checkpoint_saving_dir)
        else:
            print('Preserving existing cache directories...')

        # Ensure cache directories exist
        dataset_cache_dir.mkdir(parents=True, exist_ok=True)
        model_cache_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_saving_dir.mkdir(parents=True, exist_ok=True)

        print(f'Dataset: {args.dataset_name}')
        print(f'Dataset cache: {dataset_cache_dir}')
        print(f'Model cache: {model_cache_dir}')

        # Download and cache dataset
        print('Downloading and caching dataset...')
        dataset = load_dataset(args.dataset_name,
                               split='train',
                               cache_dir=str(dataset_cache_dir),
                               num_proc=num_proc)
        print(f'Dataset downloaded successfully. Size: {len(dataset)} examples')

        if args.flush_cache:
            flush_filesystem_cache([dataset_cache_dir])

        # Download and cache model
        print('Downloading and caching model...')
        _ = model_class.from_pretrained(args.model_id,
                                        attn_implementation='eager',
                                        cache_dir=str(model_cache_dir))
        print(f'Model downloaded and cached at: {model_cache_dir}')

        if args.flush_cache:
            flush_filesystem_cache([model_cache_dir])

        print(f'Completed processing directory {i+1}/{len(regular_dirs)}')

        # Final flush of all cache paths if requested
        if args.flush_cache:
            cache_paths = [dataset_cache_dir, model_cache_dir]
            flush_filesystem_cache(cache_paths)

        # Save the first regular directory as our source for copying to S3
        if temp_cache_dir is None:
            temp_cache_dir = base_path

    # Now copy from regular directories to S3 directories
    if s3_dirs and temp_cache_dir:
        print('\n=== Copying cached data to S3 directories ===')
        source_dataset_cache = temp_cache_dir / 'dataset_cache'
        source_model_cache = temp_cache_dir / 'model_cache'
        source_checkpoints = temp_cache_dir / 'checkpoints'

        for i, s3_base_path in enumerate(s3_dirs):
            print(f'\nCopying to S3 directory {i+1}/{len(s3_dirs)}: '
                  f'{s3_base_path}')

            s3_base_path = Path(s3_base_path)
            s3_dataset_cache = s3_base_path / 'dataset_cache'
            s3_model_cache = s3_base_path / 'model_cache'
            s3_checkpoints = s3_base_path / 'checkpoints'

            # Create S3 directories
            try:
                s3_dataset_cache.mkdir(parents=True, exist_ok=True)
                s3_model_cache.mkdir(parents=True, exist_ok=True)
                s3_checkpoints.mkdir(parents=True, exist_ok=True)

                # Copy dataset cache
                if source_dataset_cache.exists():
                    print(f'Copying dataset cache from {source_dataset_cache} '
                          f'to {s3_dataset_cache}')
                    try:
                        shutil.copytree(source_dataset_cache,
                                        s3_dataset_cache,
                                        dirs_exist_ok=True)
                        print('Dataset cache copied successfully')
                    except Exception as e:  # pylint: disable=broad-except
                        print(f'Warning: Failed to copy dataset cache: {e}')

                # Copy model cache
                if source_model_cache.exists():
                    print(f'Copying model cache from {source_model_cache} '
                          f'to {s3_model_cache}')
                    try:
                        shutil.copytree(source_model_cache,
                                        s3_model_cache,
                                        dirs_exist_ok=True)
                        print('Model cache copied successfully')
                    except Exception as e:  # pylint: disable=broad-except
                        print(f'Warning: Failed to copy model cache: {e}')

                # Copy checkpoints directory (even if empty)
                if source_checkpoints.exists():
                    print(f'Copying checkpoints from {source_checkpoints} '
                          f'to {s3_checkpoints}')
                    try:
                        shutil.copytree(source_checkpoints,
                                        s3_checkpoints,
                                        dirs_exist_ok=True)
                        print('Checkpoints copied successfully')
                    except Exception as e:  # pylint: disable=broad-except
                        print(f'Warning: Failed to copy checkpoints: {e}')

            except Exception as e:  # pylint: disable=broad-except
                print(f'Error setting up S3 directory {s3_base_path}: {e}')
                print('Skipping this S3 directory...')
                continue

            print(f'Completed copying to S3 directory {i+1}/{len(s3_dirs)}')

    print('\n=== Download and caching completed ===')


if __name__ == '__main__':
    main()
