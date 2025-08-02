# Dataset Loading Performance Benchmark

This benchmark comprehensively tests dataset loading performance across different storage types, data formats, and loading configurations to help you optimize your ML training pipelines.

## Overview

The benchmark compares:

### Storage Types
- **S3**: Cloud object storage (with caching)
- **NVMe**: Local SSD storage
- **HuggingFace Datasets**: Optimized dataset library

### Data Formats
- **CSV**: Human-readable, widely compatible
- **Parquet**: Columnar, compressed format
- **HDF5**: Scientific data format with compression
- **PyTorch Tensors**: Native PyTorch format

### Data Types
- **Tabular**: Structured data with numerical and categorical features
- **Text**: Natural language data for NLP tasks
- **Images**: Computer vision datasets

### Configuration Variables
- **Batch sizes**: 16, 32, 64, 128, 256
- **DataLoader workers**: 1, 4, 8, 16
- **Dataset sizes**: Small (1K), Medium (100K), Large (1M+ samples)

## Usage

### Basic Usage

```bash
# Set your S3 bucket
export S3_BUCKET=your-bucket-name

# Launch the benchmark
sky launch data_loading.yaml -c data-loading-benchmark
```

### Advanced Configuration

```bash
# Test all formats and sizes (longer run)
S3_BUCKET=your-bucket sky launch data_loading.yaml -c data-loading-benchmark --env TEST_ALL_FORMATS=true --env TEST_ALL_SIZES=true

# Custom worker and batch size configurations
S3_BUCKET=your-bucket sky launch data_loading.yaml -c data-loading-benchmark --env WORKER_CONFIGS="2,8,16" --env BATCH_SIZES="32,128"
```

## What It Measures

### Performance Metrics
- **Load Time**: Time to iterate through entire dataset
- **Throughput**: Samples processed per second
- **Memory Usage**: Peak memory consumption during loading
- **File Size**: Storage efficiency of different formats

### Analysis Dimensions
- **Storage comparison**: S3 vs NVMe vs HuggingFace performance
- **Format comparison**: Which format loads fastest for each data type
- **Scaling analysis**: How performance scales with workers and batch size
- **Memory efficiency**: Memory usage vs performance trade-offs

## Output Files

The benchmark generates several result files:

```
/tmp/data_loading_benchmark_results.json    # Comprehensive results
/tmp/memory_benchmark_results.json          # Memory-focused analysis
/datasets_s3/generation_results.json        # Dataset generation metrics
```

## Example Results

Typical findings from the benchmark:

### Format Performance (samples/sec)
```
PyTorch Tensors:  ~50,000 samples/sec
Parquet:         ~25,000 samples/sec
HDF5:            ~20,000 samples/sec
CSV:             ~5,000 samples/sec
```

### Storage Performance
```
NVMe:            ~3x faster than S3
HuggingFace:     ~2x faster than S3 (with optimizations)
S3 (cached):     Baseline performance
```

### Optimal Configurations
- **High Throughput**: NVMe + PyTorch tensors + 8 workers + batch size 64
- **Low Memory**: S3 + Parquet + 4 workers + batch size 32
- **Balanced**: NVMe + HDF5 + 8 workers + batch size 64

## Understanding the Results

### Key Performance Indicators

1. **Throughput (samples/sec)**: Higher is better
   - Indicates how fast you can feed data to your model
   - Critical for training efficiency

2. **Memory Usage (MB)**: Lower is generally better
   - Important for resource-constrained environments
   - Affects maximum batch size you can use

3. **Load Time (seconds)**: Lower is better
   - Total time to process entire dataset
   - Includes format parsing and data transformation overhead

### When to Use Each Format

#### CSV
- ✅ Human-readable, widely compatible
- ✅ Easy to inspect and debug
- ❌ Slowest loading performance
- ❌ Largest file sizes
- **Best for**: Small datasets, prototyping, data exploration

#### Parquet
- ✅ Good compression, faster than CSV
- ✅ Efficient for columnar operations
- ✅ Good ecosystem support
- ❌ Binary format (harder to inspect)
- **Best for**: Medium to large tabular datasets, analytics workflows

#### HDF5
- ✅ Excellent compression
- ✅ Supports complex data structures
- ✅ Good for scientific data
- ❌ More complex API
- **Best for**: Large datasets, scientific computing, when you need compression

#### PyTorch Tensors
- ✅ Fastest loading for training
- ✅ Native PyTorch format
- ✅ Preserves tensor properties
- ❌ PyTorch-specific, larger file sizes
- **Best for**: Production training pipelines, when speed is critical

### Storage Recommendations

#### Use S3 when:
- You need shared access across teams
- Working with very large datasets (>100GB)
- Cost is more important than speed
- You have good network bandwidth

#### Use NVMe when:
- Maximum performance is required
- Working with frequently accessed datasets
- You have sufficient local storage
- Training on single machines

#### Use HuggingFace Datasets when:
- Working with common ML datasets
- You want built-in preprocessing
- Need streaming capabilities
- Working with very large datasets that don't fit in memory

## Customization

### Adding Custom Datasets

Edit `generate_datasets.py` to add your own data generation logic:

```python
def generate_custom_data(size):
    # Your custom data generation logic
    return your_dataframe
```

### Testing Custom Formats

Extend `train.py` to support additional formats:

```python
elif format_type == 'your_format':
    data = load_your_format(dataset_path)
```

## Troubleshooting

### Common Issues

1. **Out of Memory**: Reduce batch size or number of workers
2. **Slow S3 Performance**: Ensure good network connectivity
3. **Missing Files**: Check that dataset generation completed successfully
4. **Format Errors**: Verify data types match expected formats

### Performance Tips

1. **For maximum speed**: Use NVMe + PyTorch tensors + optimal worker count
2. **For memory efficiency**: Use smaller batch sizes and fewer workers
3. **For storage efficiency**: Use Parquet or HDF5 with compression
4. **For debugging**: Start with CSV format and small datasets

## Contributing

To add new benchmarks or improve existing ones:

1. Add new data types in `generate_datasets.py`
2. Extend benchmarking logic in `train.py`
3. Update configuration options in `data_loading.yaml`
4. Add new analysis dimensions in the `analyze_results()` function 