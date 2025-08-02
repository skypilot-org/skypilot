#!/usr/bin/env python3
"""
Dataset generation script for comprehensive data loading benchmarks.
Creates datasets in multiple formats and sizes for performance testing.
"""

import argparse
import json
import os
from pathlib import Path
import time

import h5py
import numpy as np
import pandas as pd
from PIL import Image
import torch


def generate_tabular_data(size, num_features=100):
    """Generate synthetic tabular data"""
    np.random.seed(42)  # For reproducibility
    
    # Generate random features
    data = {
        'id': range(size),
        'label': np.random.randint(0, 10, size),  # 10 classes
    }
    
    # Add numerical features
    for i in range(num_features):
        data[f'feature_{i}'] = np.random.randn(size)
    
    # Add some categorical features
    categories = ['A', 'B', 'C', 'D', 'E']
    for i in range(10):
        data[f'category_{i}'] = np.random.choice(categories, size)
    
    return pd.DataFrame(data)

def generate_text_data(size):
    """Generate synthetic text data"""
    np.random.seed(42)
    
    # Simple text generation
    words = ['the', 'quick', 'brown', 'fox', 'jumps', 'over', 'lazy', 'dog', 
             'hello', 'world', 'machine', 'learning', 'artificial', 'intelligence',
             'data', 'science', 'python', 'pytorch', 'benchmark', 'performance']
    
    data = []
    for i in range(size):
        # Generate sentence of random length
        sentence_length = np.random.randint(5, 20)
        sentence = ' '.join(np.random.choice(words, sentence_length))
        label = np.random.randint(0, 3)  # 3 sentiment classes
        data.append({
            'id': i,
            'text': sentence,
            'label': label,
            'length': len(sentence),
            'word_count': sentence_length
        })
    
    return pd.DataFrame(data)

def generate_image_data(size, image_size=(32, 32, 3)):
    """Generate synthetic image data"""
    np.random.seed(42)
    
    height, width, channels = image_size
    images = np.random.randint(0, 256, (size, height, width, channels), dtype=np.uint8)
    labels = np.random.randint(0, 10, size)  # 10 classes like CIFAR-10
    
    return images, labels

def save_csv_dataset(df, filepath):
    """Save dataset as CSV"""
    print(f"Saving CSV dataset to {filepath}")
    start_time = time.time()
    df.to_csv(filepath, index=False)
    save_time = time.time() - start_time
    file_size = os.path.getsize(filepath) / (1024**2)  # MB
    print(f"CSV saved in {save_time:.2f}s, size: {file_size:.2f} MB")
    return save_time, file_size

def save_parquet_dataset(df, filepath):
    """Save dataset as Parquet"""
    print(f"Saving Parquet dataset to {filepath}")
    start_time = time.time()
    df.to_parquet(filepath, index=False, engine='pyarrow')
    save_time = time.time() - start_time
    file_size = os.path.getsize(filepath) / (1024**2)  # MB
    print(f"Parquet saved in {save_time:.2f}s, size: {file_size:.2f} MB")
    return save_time, file_size

def save_hdf5_dataset(data, filepath, dataset_name='data'):
    """Save dataset as HDF5"""
    print(f"Saving HDF5 dataset to {filepath}")
    start_time = time.time()
    
    with h5py.File(filepath, 'w') as f:
        if isinstance(data, pd.DataFrame):
            # For tabular data
            for col in data.columns:
                if data[col].dtype == 'object':
                    # Handle string columns
                    f.create_dataset(col, data=data[col].astype(str).values.astype('S'))
                else:
                    f.create_dataset(col, data=data[col].values)
        elif isinstance(data, tuple):
            # For image data
            images, labels = data
            f.create_dataset('images', data=images)
            f.create_dataset('labels', data=labels)
        else:
            f.create_dataset(dataset_name, data=data)
    
    save_time = time.time() - start_time
    file_size = os.path.getsize(filepath) / (1024**2)  # MB
    print(f"HDF5 saved in {save_time:.2f}s, size: {file_size:.2f} MB")
    return save_time, file_size

def save_pytorch_dataset(data, filepath):
    """Save dataset as PyTorch tensors"""
    print(f"Saving PyTorch dataset to {filepath}")
    start_time = time.time()
    
    if isinstance(data, pd.DataFrame):
        # Convert DataFrame to tensors
        tensor_data = {}
        for col in data.columns:
            if data[col].dtype == 'object':
                # Skip string columns for tensors
                continue
            tensor_data[col] = torch.tensor(data[col].values)
        torch.save(tensor_data, filepath)
    elif isinstance(data, tuple):
        # For image data
        images, labels = data
        tensor_data = {
            'images': torch.tensor(images),
            'labels': torch.tensor(labels)
        }
        torch.save(tensor_data, filepath)
    else:
        torch.save(data, filepath)
    
    save_time = time.time() - start_time
    file_size = os.path.getsize(filepath) / (1024**2)  # MB
    print(f"PyTorch saved in {save_time:.2f}s, size: {file_size:.2f} MB")
    return save_time, file_size

def generate_datasets_for_size(size_name, size, s3_dir, nvme_dir):
    """Generate all format datasets for a given size"""
    print(f"\n=== Generating {size_name} datasets ({size:,} samples) ===")
    
    results = {
        'size_name': size_name,
        'num_samples': size,
        'formats': {}
    }
    
    # Create subdirectories
    s3_size_dir = os.path.join(s3_dir, size_name)
    nvme_size_dir = os.path.join(nvme_dir, size_name)
    os.makedirs(s3_size_dir, exist_ok=True)
    os.makedirs(nvme_size_dir, exist_ok=True)
    
    # Generate tabular data
    print(f"Generating tabular data with {size:,} samples...")
    tabular_df = generate_tabular_data(size)
    
    # Generate text data
    print(f"Generating text data with {size:,} samples...")
    text_df = generate_text_data(size)
    
    # Generate image data (limit size for memory)
    image_size = min(size, 50000)  # Limit image datasets to avoid memory issues
    print(f"Generating image data with {image_size:,} samples...")
    image_data = generate_image_data(image_size)
    
    # Save in all formats to both locations
    formats = ['csv', 'parquet', 'hdf5', 'pytorch']
    
    for fmt in formats:
        print(f"\n--- Saving {fmt.upper()} format ---")
        results['formats'][fmt] = {'s3': {}, 'nvme': {}}
        
        # Tabular data
        for data_type, data in [('tabular', tabular_df), ('text', text_df)]:
            for location, base_dir in [('s3', s3_size_dir), ('nvme', nvme_size_dir)]:
                if fmt == 'csv':
                    filepath = os.path.join(base_dir, f'{data_type}_{size_name}.csv')
                    save_time, file_size = save_csv_dataset(data, filepath)
                elif fmt == 'parquet':
                    filepath = os.path.join(base_dir, f'{data_type}_{size_name}.parquet')
                    save_time, file_size = save_parquet_dataset(data, filepath)
                elif fmt == 'hdf5':
                    filepath = os.path.join(base_dir, f'{data_type}_{size_name}.h5')
                    save_time, file_size = save_hdf5_dataset(data, filepath)
                elif fmt == 'pytorch':
                    filepath = os.path.join(base_dir, f'{data_type}_{size_name}.pt')
                    save_time, file_size = save_pytorch_dataset(data, filepath)
                
                results['formats'][fmt][location][data_type] = {
                    'save_time': save_time,
                    'file_size_mb': file_size,
                    'filepath': filepath
                }
        
        # Image data
        for location, base_dir in [('s3', s3_size_dir), ('nvme', nvme_size_dir)]:
            if fmt == 'hdf5':
                filepath = os.path.join(base_dir, f'images_{size_name}.h5')
                save_time, file_size = save_hdf5_dataset(image_data, filepath)
            elif fmt == 'pytorch':
                filepath = os.path.join(base_dir, f'images_{size_name}.pt')
                save_time, file_size = save_pytorch_dataset(image_data, filepath)
            else:
                # Skip CSV/Parquet for image data
                continue
            
            results['formats'][fmt][location]['images'] = {
                'save_time': save_time,
                'file_size_mb': file_size,
                'filepath': filepath,
                'num_samples': image_size
            }
    
    return results

def main():
    parser = argparse.ArgumentParser(description="Generate datasets for benchmarking")
    parser.add_argument("--s3_dir", default="/datasets_s3", help="S3 directory for datasets")
    parser.add_argument("--nvme_dir", default="/tmp/datasets_nvme", help="NVMe directory for datasets")
    parser.add_argument("--generate_all", action="store_true", help="Generate all size variants")
    parser.add_argument("--sizes", default="small,medium,large", help="Sizes to generate (comma-separated)")
    args = parser.parse_args()
    
    # Define dataset sizes
    size_configs = {
        'small': 1000,
        'medium': 100000,
        'large': 1000000
    }
    
    if args.generate_all:
        sizes_to_generate = size_configs.keys()
    else:
        sizes_to_generate = args.sizes.split(',')
    
    print(f"=== Dataset Generation Starting ===")
    print(f"S3 directory: {args.s3_dir}")
    print(f"NVMe directory: {args.nvme_dir}")
    print(f"Sizes to generate: {list(sizes_to_generate)}")
    
    # Create base directories
    os.makedirs(args.s3_dir, exist_ok=True)
    os.makedirs(args.nvme_dir, exist_ok=True)
    
    all_results = []
    
    for size_name in sizes_to_generate:
        if size_name not in size_configs:
            print(f"Warning: Unknown size '{size_name}', skipping")
            continue
        
        size = size_configs[size_name]
        try:
            results = generate_datasets_for_size(size_name, size, args.s3_dir, args.nvme_dir)
            all_results.append(results)
        except Exception as e:
            print(f"Error generating {size_name} datasets: {e}")
            continue
    
    # Save generation results
    generation_results = {
        'timestamp': time.time(),
        'total_datasets_generated': len(all_results),
        'datasets': all_results,
        'summary': {
            'sizes_generated': list(sizes_to_generate),
            'formats': ['csv', 'parquet', 'hdf5', 'pytorch'],
            'data_types': ['tabular', 'text', 'images']
        }
    }
    
    results_file = os.path.join(args.s3_dir, 'generation_results.json')
    with open(results_file, 'w') as f:
        json.dump(generation_results, f, indent=2)
    
    print(f"\n=== Dataset Generation Complete ===")
    print(f"Generated {len(all_results)} dataset size variants")
    print(f"Results saved to: {results_file}")
    
    # Print summary
    print(f"\nGenerated datasets summary:")
    for result in all_results:
        size_name = result['size_name']
        num_samples = result['num_samples']
        print(f"  {size_name}: {num_samples:,} samples")
        
        # List files in S3
        s3_files = []
        for fmt in result['formats']:
            for data_type in result['formats'][fmt]['s3']:
                filepath = result['formats'][fmt]['s3'][data_type]['filepath']
                file_size = result['formats'][fmt]['s3'][data_type]['file_size_mb']
                s3_files.append(f"    {os.path.basename(filepath)}: {file_size:.1f} MB")
        
        if s3_files:
            print(f"    Files in S3:")
            for file_info in s3_files[:5]:  # Show first 5
                print(file_info)
            if len(s3_files) > 5:
                print(f"    ... and {len(s3_files) - 5} more files")

if __name__ == "__main__":
    main() 