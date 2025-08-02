#!/usr/bin/env python3
"""
Comprehensive dataset loading benchmark script.
Tests loading performance across different formats, storage types, and configurations.
"""

import argparse
import gc
import json
import os
from pathlib import Path
import time
import warnings

from accelerate import Accelerator
from datasets import Dataset as HFDataset
from datasets import load_dataset
import h5py
from memory_profiler import profile
import numpy as np
import pandas as pd
import psutil
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.utils.data import Dataset
from torch.utils.data import TensorDataset

warnings.filterwarnings("ignore")

class CustomDataset(Dataset):
    """Custom dataset class for different data formats"""
    
    def __init__(self, data, data_type='tabular'):
        self.data = data
        self.data_type = data_type
        
        if data_type == 'tabular':
            # Extract features and labels
            if isinstance(data, dict):
                self.features = torch.stack([v for k, v in data.items() if k != 'label' and k != 'id'])
                self.labels = data.get('label', torch.zeros(len(list(data.values())[0])))
            elif hasattr(data, 'columns'):
                feature_cols = [col for col in data.columns if col not in ['label', 'id']]
                self.features = torch.tensor(data[feature_cols].select_dtypes(include=[np.number]).values, dtype=torch.float32)
                self.labels = torch.tensor(data['label'].values if 'label' in data.columns else np.zeros(len(data)), dtype=torch.long)
            else:
                raise ValueError("Unsupported data format for tabular dataset")
                
        elif data_type == 'text':
            if isinstance(data, dict):
                self.texts = data.get('text', [])
                self.labels = data.get('label', torch.zeros(len(self.texts)))
            elif hasattr(data, 'columns'):
                self.texts = data['text'].tolist() if 'text' in data.columns else []
                self.labels = torch.tensor(data['label'].values if 'label' in data.columns else np.zeros(len(data)), dtype=torch.long)
            else:
                raise ValueError("Unsupported data format for text dataset")
        
        elif data_type == 'images':
            if isinstance(data, dict):
                self.images = data['images']
                self.labels = data['labels']
            elif isinstance(data, tuple):
                self.images, self.labels = data
            else:
                raise ValueError("Unsupported data format for image dataset")
            
            # Ensure proper tensor format
            if not isinstance(self.images, torch.Tensor):
                self.images = torch.tensor(self.images, dtype=torch.float32)
            if not isinstance(self.labels, torch.Tensor):
                self.labels = torch.tensor(self.labels, dtype=torch.long)
    
    def __len__(self):
        if self.data_type == 'tabular':
            return len(self.features)
        elif self.data_type == 'text':
            return len(self.texts)
        elif self.data_type == 'images':
            return len(self.images)
    
    def __getitem__(self, idx):
        if self.data_type == 'tabular':
            return self.features[idx], self.labels[idx]
        elif self.data_type == 'text':
            # Simple text encoding (in real scenario, use tokenizer)
            text = self.texts[idx]
            # Convert text to simple numerical representation
            text_tensor = torch.tensor([hash(word) % 1000 for word in text.split()[:50]], dtype=torch.float32)
            if len(text_tensor) < 50:
                text_tensor = torch.cat([text_tensor, torch.zeros(50 - len(text_tensor))])
            return text_tensor, self.labels[idx]
        elif self.data_type == 'images':
            return self.images[idx], self.labels[idx]

def benchmark_data_loading(dataset_path, data_type, format_type, storage_type, batch_size, num_workers, iterations=5):
    """Benchmark dataset loading performance"""
    print(f"\n=== Benchmarking {storage_type} {format_type} {data_type} loading ===")
    print(f"Dataset: {dataset_path}")
    print(f"Batch size: {batch_size}, Workers: {num_workers}, Iterations: {iterations}")
    
    results = {
        'dataset_path': dataset_path,
        'data_type': data_type,
        'format_type': format_type,
        'storage_type': storage_type,
        'batch_size': batch_size,
        'num_workers': num_workers,
        'iterations': iterations,
        'load_times': [],
        'throughput_samples_per_sec': [],
        'memory_usage_mb': [],
        'file_size_mb': 0,
        'total_samples': 0,
        'error': None
    }
    
    if not os.path.exists(dataset_path):
        results['error'] = f"Dataset file not found: {dataset_path}"
        return results
    
    # Get file size
    results['file_size_mb'] = os.path.getsize(dataset_path) / (1024**2)
    
    try:
        # Load data based on format
        if format_type == 'csv':
            data = pd.read_csv(dataset_path)
        elif format_type == 'parquet':
            data = pd.read_parquet(dataset_path)
        elif format_type == 'hdf5':
            with h5py.File(dataset_path, 'r') as f:
                if data_type == 'images':
                    data = {
                        'images': f['images'][:],
                        'labels': f['labels'][:]
                    }
                else:
                    # Load all datasets as dictionary
                    data = {key: f[key][:] for key in f.keys()}
        elif format_type == 'pytorch':
            data = torch.load(dataset_path, map_location='cpu')
        else:
            results['error'] = f"Unsupported format: {format_type}"
            return results
        
        # Create dataset
        dataset = CustomDataset(data, data_type)
        results['total_samples'] = len(dataset)
        
        # Benchmark multiple iterations
        for iteration in range(iterations):
            print(f"  Iteration {iteration + 1}/{iterations}")
            
            # Measure memory before loading
            process = psutil.Process(os.getpid())
            memory_before = process.memory_info().rss / 1024 / 1024  # MB
            
            # Create DataLoader
            dataloader = DataLoader(
                dataset,
                batch_size=batch_size,
                shuffle=False,  # Don't shuffle for consistent timing
                num_workers=num_workers,
                pin_memory=True,
                persistent_workers=num_workers > 0
            )
            
            # Benchmark loading
            start_time = time.time()
            total_samples = 0
            
            for batch_idx, (inputs, labels) in enumerate(dataloader):
                total_samples += len(inputs)
                # Simulate some processing
                if isinstance(inputs, torch.Tensor):
                    _ = inputs.mean()  # Simple operation to ensure data is loaded
            
            load_time = time.time() - start_time
            
            # Measure memory after loading
            memory_after = process.memory_info().rss / 1024 / 1024  # MB
            memory_usage = memory_after - memory_before
            
            # Calculate throughput
            throughput = total_samples / load_time if load_time > 0 else 0
            
            results['load_times'].append(load_time)
            results['throughput_samples_per_sec'].append(throughput)
            results['memory_usage_mb'].append(memory_usage)
            
            print(f"    Load time: {load_time:.2f}s, Throughput: {throughput:.0f} samples/s, Memory: {memory_usage:.1f} MB")
            
            # Cleanup
            del dataloader
            gc.collect()
            time.sleep(1)  # Brief pause between iterations
        
        # Calculate summary statistics
        results['avg_load_time'] = np.mean(results['load_times'])
        results['std_load_time'] = np.std(results['load_times'])
        results['avg_throughput'] = np.mean(results['throughput_samples_per_sec'])
        results['max_throughput'] = np.max(results['throughput_samples_per_sec'])
        results['avg_memory_usage'] = np.mean(results['memory_usage_mb'])
        
        print(f"  Summary: Avg load time: {results['avg_load_time']:.2f}s ± {results['std_load_time']:.2f}s")
        print(f"           Avg throughput: {results['avg_throughput']:.0f} samples/s")
        print(f"           Max throughput: {results['max_throughput']:.0f} samples/s")
        print(f"           Avg memory usage: {results['avg_memory_usage']:.1f} MB")
        
    except Exception as e:
        results['error'] = str(e)
        print(f"  Error: {e}")
    
    return results

def benchmark_hf_datasets(cache_dir, batch_size, num_workers, iterations=3):
    """Benchmark HuggingFace datasets loading"""
    print(f"\n=== Benchmarking HuggingFace Datasets ===")
    
    datasets_to_test = [
        ('imdb', 'train[:1000]', 'text'),
        ('cifar10', 'train[:1000]', 'images')
    ]
    
    hf_results = []
    
    for dataset_name, split, data_type in datasets_to_test:
        print(f"\nTesting {dataset_name} dataset...")
        
        result = {
            'dataset_name': dataset_name,
            'split': split,
            'data_type': data_type,
            'storage_type': 'huggingface',
            'format_type': 'hf_dataset',
            'batch_size': batch_size,
            'num_workers': num_workers,
            'iterations': iterations,
            'load_times': [],
            'throughput_samples_per_sec': [],
            'error': None
        }
        
        try:
            # Load dataset
            for iteration in range(iterations):
                print(f"  Iteration {iteration + 1}/{iterations}")
                
                start_time = time.time()
                
                # Load HuggingFace dataset
                dataset = load_dataset(dataset_name, split=split, cache_dir=cache_dir)
                
                # Convert to PyTorch dataset for fair comparison
                if data_type == 'text':
                    # Simple text processing
                    def process_text(examples):
                        return {
                            'text': examples['text'],
                            'labels': examples['label']
                        }
                    dataset = dataset.map(process_text, batched=True)
                    torch_dataset = TensorDataset(
                        torch.ones(len(dataset), 50),  # Dummy text tensor
                        torch.tensor(dataset['labels'])
                    )
                elif data_type == 'images':
                    # Convert images
                    torch_dataset = TensorDataset(
                        torch.tensor(np.array(dataset['img'])).permute(0, 3, 1, 2).float(),
                        torch.tensor(dataset['label'])
                    )
                
                # Create DataLoader
                dataloader = DataLoader(
                    torch_dataset,
                    batch_size=batch_size,
                    shuffle=False,
                    num_workers=num_workers,
                    pin_memory=True
                )
                
                # Iterate through dataloader
                total_samples = 0
                for batch_idx, (inputs, labels) in enumerate(dataloader):
                    total_samples += len(inputs)
                    _ = inputs.mean()  # Ensure data is processed
                
                load_time = time.time() - start_time
                throughput = total_samples / load_time if load_time > 0 else 0
                
                result['load_times'].append(load_time)
                result['throughput_samples_per_sec'].append(throughput)
                
                print(f"    Load time: {load_time:.2f}s, Throughput: {throughput:.0f} samples/s")
                
                # Cleanup
                del dataloader, torch_dataset, dataset
                gc.collect()
        
        except Exception as e:
            result['error'] = str(e)
            print(f"  Error loading {dataset_name}: {e}")
        
        hf_results.append(result)
    
    return hf_results

def run_comprehensive_benchmark(args):
    """Run comprehensive benchmark across all configurations"""
    print("=== Starting Comprehensive Dataset Loading Benchmark ===")
    
    all_results = []
    
    # Test configurations
    batch_sizes = [int(x) for x in args.test_batch_sizes.split(',')]
    worker_configs = [int(x) for x in args.test_worker_configs.split(',')]
    
    # Dataset configurations to test
    size_names = ['small', 'medium'] if not args.test_all_sizes else ['small', 'medium', 'large']
    formats = ['csv', 'parquet', 'hdf5', 'pytorch'] if args.test_all_formats else ['csv', 'pytorch']
    data_types = ['tabular', 'text', 'images']
    storage_types = ['s3', 'nvme']
    
    total_tests = len(size_names) * len(formats) * len(data_types) * len(storage_types) * len(batch_sizes) * len(worker_configs)
    test_count = 0
    
    print(f"Total tests to run: {total_tests}")
    
    for size_name in size_names:
        for format_type in formats:
            for data_type in data_types:
                # Skip unsupported combinations
                if format_type in ['csv', 'parquet'] and data_type == 'images':
                    continue
                
                for storage_type in storage_types:
                    base_dir = args.s3_data_dir if storage_type == 's3' else args.nvme_data_dir
                    
                    if data_type == 'images':
                        dataset_path = os.path.join(base_dir, size_name, f'{data_type}_{size_name}.{format_type}')
                    else:
                        dataset_path = os.path.join(base_dir, size_name, f'{data_type}_{size_name}.{format_type}')
                    
                    for batch_size in batch_sizes:
                        for num_workers in worker_configs:
                            test_count += 1
                            print(f"\n[{test_count}/{total_tests}] Testing configuration:")
                            print(f"  Size: {size_name}, Format: {format_type}, Type: {data_type}")
                            print(f"  Storage: {storage_type}, Batch: {batch_size}, Workers: {num_workers}")
                            
                            result = benchmark_data_loading(
                                dataset_path, data_type, format_type, storage_type,
                                batch_size, num_workers, args.benchmark_iterations
                            )
                            
                            result['test_id'] = test_count
                            result['size_name'] = size_name
                            all_results.append(result)
    
    # Test HuggingFace datasets
    if args.hf_cache_dir:
        print(f"\n=== Testing HuggingFace Datasets ===")
        for batch_size in batch_sizes:
            for num_workers in worker_configs:
                hf_results = benchmark_hf_datasets(args.hf_cache_dir, batch_size, num_workers, args.benchmark_iterations)
                for result in hf_results:
                    result['test_id'] = test_count + 1
                    result['size_name'] = 'hf_dataset'
                    test_count += 1
                    all_results.append(result)
    
    return all_results

def analyze_results(results):
    """Analyze and generate summary of benchmark results"""
    print("\n=== Analyzing Benchmark Results ===")
    
    successful_results = [r for r in results if r.get('error') is None]
    failed_results = [r for r in results if r.get('error') is not None]
    
    print(f"Successful tests: {len(successful_results)}")
    print(f"Failed tests: {len(failed_results)}")
    
    if failed_results:
        print(f"\nFailed tests summary:")
        for result in failed_results:
            print(f"  {result['storage_type']} {result['format_type']} {result['data_type']}: {result['error']}")
    
    if not successful_results:
        print("No successful results to analyze")
        return {}
    
    # Group results by different dimensions
    analysis = {
        'summary': {
            'total_tests': len(results),
            'successful_tests': len(successful_results),
            'failed_tests': len(failed_results)
        },
        'by_storage': {},
        'by_format': {},
        'by_batch_size': {},
        'by_workers': {},
        'best_configurations': {},
        'recommendations': []
    }
    
    # Analyze by storage type
    for storage in ['s3', 'nvme', 'huggingface']:
        storage_results = [r for r in successful_results if r['storage_type'] == storage]
        if storage_results:
            avg_throughput = np.mean([r.get('avg_throughput', 0) for r in storage_results])
            analysis['by_storage'][storage] = {
                'count': len(storage_results),
                'avg_throughput': avg_throughput,
                'best_throughput': max([r.get('max_throughput', 0) for r in storage_results])
            }
    
    # Analyze by format
    for fmt in ['csv', 'parquet', 'hdf5', 'pytorch', 'hf_dataset']:
        format_results = [r for r in successful_results if r['format_type'] == fmt]
        if format_results:
            avg_throughput = np.mean([r.get('avg_throughput', 0) for r in format_results])
            analysis['by_format'][fmt] = {
                'count': len(format_results),
                'avg_throughput': avg_throughput,
                'best_throughput': max([r.get('max_throughput', 0) for r in format_results])
            }
    
    # Find best configurations
    if successful_results:
        best_throughput = max(successful_results, key=lambda x: x.get('max_throughput', 0))
        best_memory = min(successful_results, key=lambda x: x.get('avg_memory_usage', float('inf')))
        
        analysis['best_configurations'] = {
            'highest_throughput': {
                'config': f"{best_throughput['storage_type']} {best_throughput['format_type']} {best_throughput['data_type']}",
                'throughput': best_throughput.get('max_throughput', 0),
                'batch_size': best_throughput['batch_size'],
                'workers': best_throughput['num_workers']
            },
            'lowest_memory': {
                'config': f"{best_memory['storage_type']} {best_memory['format_type']} {best_memory['data_type']}",
                'memory_usage': best_memory.get('avg_memory_usage', 0),
                'batch_size': best_memory['batch_size'],
                'workers': best_memory['num_workers']
            }
        }
    
    # Generate recommendations
    storage_perf = analysis['by_storage']
    if storage_perf:
        best_storage = max(storage_perf.keys(), key=lambda x: storage_perf[x]['avg_throughput'])
        analysis['recommendations'].append(f"Best storage type: {best_storage}")
    
    format_perf = analysis['by_format']
    if format_perf:
        best_format = max(format_perf.keys(), key=lambda x: format_perf[x]['avg_throughput'])
        analysis['recommendations'].append(f"Best format: {best_format}")
    
    return analysis

def memory_benchmark(args):
    """Specialized memory usage benchmark"""
    print("=== Running Memory Usage Benchmark ===")
    
    batch_sizes = [int(x) for x in args.test_batch_sizes.split(',')]
    memory_results = []
    
    # Test memory usage with different batch sizes
    for batch_size in batch_sizes:
        print(f"\nTesting batch size: {batch_size}")
        
        # Find a sample dataset
        sample_path = os.path.join(args.s3_data_dir, 'small', 'tabular_small.pytorch')
        if not os.path.exists(sample_path):
            sample_path = os.path.join(args.nvme_data_dir, 'small', 'tabular_small.pytorch')
        
        if os.path.exists(sample_path):
            result = benchmark_data_loading(
                sample_path, 'tabular', 'pytorch', 's3',
                batch_size, 4, iterations=3
            )
            memory_results.append(result)
    
    return memory_results

def main():
    parser = argparse.ArgumentParser(description="Comprehensive dataset loading benchmark")
    parser.add_argument("--s3_data_dir", default="/datasets_s3", help="S3 data directory")
    parser.add_argument("--nvme_data_dir", default="/tmp/datasets_nvme", help="NVMe data directory")
    parser.add_argument("--hf_cache_dir", default="/tmp/hf_cache", help="HuggingFace cache directory")
    parser.add_argument("--epochs", type=int, default=2, help="Number of training epochs")
    parser.add_argument("--benchmark_iterations", type=int, default=5, help="Iterations per benchmark")
    parser.add_argument("--test_all_formats", action="store_true", help="Test all data formats")
    parser.add_argument("--test_all_sizes", action="store_true", help="Test all dataset sizes")
    parser.add_argument("--test_worker_configs", default="1,4,8", help="Worker configurations to test")
    parser.add_argument("--test_batch_sizes", default="32,64", help="Batch sizes to test")
    parser.add_argument("--analyze_results_only", action="store_true", help="Only analyze existing results")
    parser.add_argument("--memory_benchmark_only", action="store_true", help="Only run memory benchmark")
    args = parser.parse_args()
    
    accelerator = Accelerator()
    
    if args.analyze_results_only:
        # Load and analyze existing results
        results_file = "/tmp/data_loading_benchmark_results.json"
        if os.path.exists(results_file):
            with open(results_file, 'r') as f:
                results_data = json.load(f)
            
            analysis = analyze_results(results_data.get('detailed_results', []))
            
            print("\n=== COMPREHENSIVE BENCHMARK ANALYSIS ===")
            print(json.dumps(analysis, indent=2))
        else:
            print("No results file found to analyze")
        return
    
    if args.memory_benchmark_only:
        # Run memory-focused benchmark
        memory_results = memory_benchmark(args)
        
        memory_analysis = {
            'memory_benchmark': memory_results,
            'timestamp': time.time()
        }
        
        with open("/tmp/memory_benchmark_results.json", "w") as f:
            json.dump(memory_analysis, f, indent=2)
        
        print("Memory benchmark results saved to /tmp/memory_benchmark_results.json")
        return
    
    # Run comprehensive benchmark
    all_results = run_comprehensive_benchmark(args)
    
    # Analyze results
    analysis = analyze_results(all_results)
    
    # Save detailed results
    final_results = {
        'timestamp': time.time(),
        'configuration': {
            's3_data_dir': args.s3_data_dir,
            'nvme_data_dir': args.nvme_data_dir,
            'hf_cache_dir': args.hf_cache_dir,
            'test_batch_sizes': args.test_batch_sizes,
            'test_worker_configs': args.test_worker_configs,
            'benchmark_iterations': args.benchmark_iterations
        },
        'detailed_results': all_results,
        'analysis': analysis
    }
    
    results_file = "/tmp/data_loading_benchmark_results.json"
    with open(results_file, 'w') as f:
        json.dump(final_results, f, indent=2)
    
    print(f"\n=== BENCHMARK COMPLETE ===")
    print(f"Results saved to: {results_file}")
    print(f"\n=== FINAL ANALYSIS ===")
    print(json.dumps(analysis, indent=2))

if __name__ == "__main__":
    main() 