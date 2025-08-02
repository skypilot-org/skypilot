#!/usr/bin/env python3
"""
Comprehensive model loading benchmarking script.
Compares S3 vs NVMe vs HuggingFace parallel loading performance.
Tests multiple model sizes for scaling analysis.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import torch
from transformers import AutoConfig
from transformers import AutoModel
from transformers import AutoTokenizer


def evict_files_from_cache(path):
    """Evict specific files/directories from page cache using vmtouch"""
    if not os.path.exists(path):
        print(f"⚠️ Path does not exist for cache eviction: {path}")
        return
    
    print(f"🧹 Evicting {path} from page cache...")

    try:
        # Use vmtouch to evict the specific path from cache
        result = subprocess.run(['vmtouch', '-e', path], capture_output=True, text=True, check=True)
        print(f"✅ Successfully evicted {path} from cache")
        if result.stdout.strip():
            print(f"   vmtouch output: {result.stdout.strip()}")
    except subprocess.CalledProcessError as e:
        print(f"⚠️ vmtouch eviction failed: {e.stderr}")
        print("💡 Falling back to sync operation...")
        subprocess.run(['sync'], check=True)
    
    time.sleep(1)  # Brief pause to let system stabilize


def get_model_config_from_env():
    """Get model configuration from environment variables"""
    test_models_env = os.environ.get('TEST_MODELS', '')
    local_dirs_env = os.environ.get('LOCAL_MODEL_DIRS', '')
    
    if test_models_env and local_dirs_env:
        models = [m.strip() for m in test_models_env.split(',')]
        dirs = [d.strip() for d in local_dirs_env.split(',')]
        
        if len(models) != len(dirs):
            print(f"Warning: TEST_MODELS ({len(models)}) and LOCAL_MODEL_DIRS ({len(dirs)}) have different lengths")
            # Use model names as directory names if mismatch
            dirs = [m.split('/')[-1] for m in models]
        
        model_config = dict(zip(models, dirs))
    else:
        # Fallback to default configuration
        model_config = {
            "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B": "deepseek-7b",
            "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B": "deepseek-14b",
            "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B": "deepseek-32b",
            "deepseek-ai/DeepSeek-R1-Distill-Llama-70B": "deepseek-70b"
        }
    
    # Add common models for benchmarking
    model_config["bert-base-uncased"] = "bert-base-uncased"
    model_config["distilbert-base-uncased"] = "distilbert-base-uncased"
    
    print(f"Model configuration: {model_config}")
    return model_config


def benchmark_hf_parallel_loading(model_name, description, num_workers=4):
    """Benchmark HuggingFace model loading comparing S3 vs NVMe storage"""
    print(f"\n=== Benchmarking {description} ===")
    print("Comparing S3 Mount vs NVMe storage performance")
    
    # Get model configuration from environment variables
    model_config = get_model_config_from_env()
    
    # Determine local path
    local_dir = model_config.get(model_name, model_name.split('/')[-1])
    s3_path = f"/checkpoints_s3/{local_dir}"
    nvme_path = f"/tmp/checkpoints_nvme/{local_dir}"
    
    all_results = []
    
    # 1. Benchmark S3 mount loading
    print(f"\n--- Testing S3 Mount Loading: {s3_path} ---")
    if os.path.exists(s3_path):
        s3_result = benchmark_single_location(model_name, s3_path, "S3 Mount", num_workers, use_parallel=True)
        if s3_result:
            all_results.append(s3_result)
    else:
        print(f"S3 path not found: {s3_path}")
    
    # 2. Copy to NVMe and benchmark
    print(f"\n--- Copying to NVMe and Testing: {nvme_path} ---")
    if os.path.exists(s3_path):
        # Check if S3 model has actual weight files
        s3_model_files = os.listdir(s3_path)
        s3_safetensors_files = [f for f in s3_model_files if f.endswith('.safetensors')]
        s3_bin_files = [f for f in s3_model_files if f.endswith('.bin')]
        s3_has_safetensors_index = any(f == 'model.safetensors.index.json' for f in s3_model_files)
        
        # Check if S3 model is complete
        s3_model_complete = (s3_safetensors_files or s3_bin_files or 
                            (s3_has_safetensors_index and s3_safetensors_files))
        
        if not s3_model_complete:
            print(f"⚠️ S3 model incomplete, downloading directly to NVMe instead...")
            if os.path.exists(nvme_path):
                shutil.rmtree(nvme_path)
            
            os.makedirs(nvme_path, exist_ok=True)
            
            print(f"🔽 Downloading {model_name} directly to {nvme_path}...")
            download_start = time.time()
            
            # Use subprocess to call huggingface-cli download
            import subprocess
            cmd = [
                'huggingface-cli', 'download', model_name, 
                '--local-dir', nvme_path, 
                '--local-dir-use-symlinks', 'False',
                '--resume-download'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            download_time = time.time() - download_start
            
            if result.returncode == 0:
                print(f"✅ Direct download completed in {download_time:.2f}s")
                
                # Get size of downloaded model
                def get_dir_size(path):
                    total = 0
                    for dirpath, dirnames, filenames in os.walk(path):
                        for f in filenames:
                            fp = os.path.join(dirpath, f)
                            total += os.path.getsize(fp)
                    return total
                
                model_size_bytes = get_dir_size(nvme_path)
                model_size_gb = model_size_bytes / (1024**3)
                download_speed = model_size_gb / download_time
                
                print(f"Downloaded: {model_size_gb:.2f} GB ({download_speed:.2f} GB/s)")
            else:
                print(f"❌ Download failed: {result.stderr}")
                print(f"Will try copying incomplete S3 model...")
                # Fall back to copying S3 model
                shutil.copytree(s3_path, nvme_path)
        else:
            # S3 model is complete, copy it
            if os.path.exists(nvme_path):
                shutil.rmtree(nvme_path)
            
            # Drop caches before copy operation for fair measurement
            evict_files_from_cache(s3_path)
            
            print(f"Copying {s3_path} to {nvme_path}...")
            copy_start = time.time()
            shutil.copytree(s3_path, nvme_path)
            copy_time = time.time() - copy_start
            
            # Get size of copied model
            def get_dir_size(path):
                total = 0
                for dirpath, dirnames, filenames in os.walk(path):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        total += os.path.getsize(fp)
                return total
            
            model_size_bytes = get_dir_size(nvme_path)
            model_size_gb = model_size_bytes / (1024**3)
            copy_speed = model_size_gb / copy_time
            
            print(f"Copy completed: {model_size_gb:.2f} GB in {copy_time:.2f}s ({copy_speed:.2f} GB/s)")
        
        # Now benchmark NVMe loading
        nvme_result = benchmark_single_location(model_name, nvme_path, "NVMe", num_workers, use_parallel=True)
        if nvme_result:
            if 's3_model_complete' in locals() and s3_model_complete and 'copy_time' in locals():
                nvme_result["copy_time"] = copy_time
                nvme_result["copy_speed_gbps"] = copy_speed
            all_results.append(nvme_result)
    else:
        print(f"Source S3 path not found: {s3_path}")
        print(f"Downloading {model_name} directly to NVMe...")
        
        if os.path.exists(nvme_path):
            shutil.rmtree(nvme_path)
        
        os.makedirs(nvme_path, exist_ok=True)
        
        print(f"🔽 Downloading {model_name} directly to {nvme_path}...")
        download_start = time.time()
        
        # Use subprocess to call huggingface-cli download
        import subprocess
        cmd = [
            'huggingface-cli', 'download', model_name, 
            '--local-dir', nvme_path, 
            '--local-dir-use-symlinks', 'False',
            '--resume-download'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        download_time = time.time() - download_start
        
        if result.returncode == 0:
            print(f"✅ Direct download completed in {download_time:.2f}s")
            
            # Get size of downloaded model
            def get_dir_size(path):
                total = 0
                for dirpath, dirnames, filenames in os.walk(path):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        total += os.path.getsize(fp)
                return total
            
            model_size_bytes = get_dir_size(nvme_path)
            model_size_gb = model_size_bytes / (1024**3)
            download_speed = model_size_gb / download_time
            
            print(f"Downloaded: {model_size_gb:.2f} GB ({download_speed:.2f} GB/s)")
            
            # Now benchmark NVMe loading
            nvme_result = benchmark_single_location(model_name, nvme_path, "NVMe", num_workers, use_parallel=True)
            if nvme_result:
                all_results.append(nvme_result)
        else:
            print(f"❌ Download failed: {result.stderr}")
    
    # Compare results
    if len(all_results) > 1:
        print(f"\n=== Storage Performance Comparison for {model_name} ===")
        
        # Sort by load speed
        sorted_results = sorted(all_results, key=lambda x: x["load_speed_gbps"], reverse=True)
        
        for i, result in enumerate(sorted_results):
            rank_emoji = ["🥇", "🥈"][i] if i < 2 else f"{i+1}."
            print(f"{rank_emoji} {result['storage_type']}: {result['load_speed_gbps']:.2f} GB/s ({result['load_time']:.2f}s)")
        
        # Calculate speedup
        if len(sorted_results) == 2:
            fastest = sorted_results[0]
            slowest = sorted_results[1] 
            speedup = fastest["load_speed_gbps"] / slowest["load_speed_gbps"]
            print(f"\n🚀 {fastest['storage_type']} is {speedup:.2f}x faster than {slowest['storage_type']}")
    
    return all_results


def benchmark_single_location(model_name, load_path, storage_type, num_workers, use_parallel=True):
    """Benchmark loading from a single location"""
    
    # Configure parallel loading based on parameter
    if use_parallel:
        os.environ['HF_ENABLE_PARALLEL_LOADING'] = 'true'
        os.environ['HF_PARALLEL_LOADING_WORKERS'] = str(num_workers)
        print(f"Using parallel loading with {num_workers} workers")
        loading_desc = f"{storage_type} (Parallel)"
    else:
        os.environ.pop('HF_ENABLE_PARALLEL_LOADING', None)
        os.environ.pop('HF_PARALLEL_LOADING_WORKERS', None)
        print(f"Using standard loading (no parallel)")
        loading_desc = f"{storage_type} (Standard)"
        
    # Load model with appropriate settings
    print(f"Loading from {load_path}...")
    
    # Check if the model directory has actual model files
    if os.path.isdir(load_path):
        model_files = os.listdir(load_path)
        safetensors_files = [f for f in model_files if f.endswith('.safetensors')]
        bin_files = [f for f in model_files if f.endswith('.bin')]
        
        # Also check for indexed safetensors files
        has_safetensors_index = any(f == 'model.safetensors.index.json' for f in model_files)
        
        if not safetensors_files and not bin_files and not has_safetensors_index:
            print(f"❌ No model weight files found in {load_path}")
            print(f"Available files: {model_files}")
            return None
        elif has_safetensors_index and not safetensors_files:
            print(f"⚠️ Found safetensors index but missing actual safetensors files in {load_path}")
            print(f"Available files: {model_files}")
            print("💡 The model download may be incomplete. Try re-downloading.")
            return None
    
    start_time = time.time()

    print(f"📁 Reading model files from {load_path}...")
    file_read_start = time.time()
    
    # Load tokenizer first
    tokenizer_start = time.time()
    tokenizer = AutoTokenizer.from_pretrained(load_path, trust_remote_code=True)
    tokenizer_time = time.time() - tokenizer_start
    
    # Load model
    model_load_start = time.time()
    model = AutoModel.from_pretrained(load_path, trust_remote_code=True)
    model_load_time = time.time() - model_load_start
    
    total_load_time = time.time() - start_time
    
    # Calculate model size using built-in method
    model_size_bytes = model.get_memory_footprint()
    model_size_gb = model_size_bytes / (1024**3)
    param_count = sum(p.numel() for p in model.parameters())
    
    # Calculate file sizes on disk
    def get_dir_size_and_files(path):
        total_size = 0
        file_count = 0
        largest_files = []
        
        for dirpath, dirnames, filenames in os.walk(path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                try:
                    size = os.path.getsize(filepath)
                    total_size += size
                    file_count += 1
                    largest_files.append((filename, size))
                except OSError:
                    pass
        
        # Sort by size and get top 5
        largest_files.sort(key=lambda x: x[1], reverse=True)
        return total_size, file_count, largest_files[:5]
    
    disk_size_bytes, file_count, largest_files = get_dir_size_and_files(load_path)
    disk_size_gb = disk_size_bytes / (1024**3)
    
    print(f"✅ Successfully loaded from {loading_desc}")
    print(f"📊 Loading Performance Breakdown:")
    print(f"   Tokenizer load: {tokenizer_time:.2f}s")
    print(f"   Model load: {model_load_time:.2f}s ({model_load_time/total_load_time*100:.1f}% of total)")
    print(f"   Total time: {total_load_time:.2f}s")
    print(f"📁 File System Analysis:")
    print(f"   Files on disk: {file_count} files, {disk_size_gb:.2f} GB")
    print(f"   Model in memory: {model_size_gb:.2f} GB")
    print(f"   Compression ratio: {disk_size_gb/model_size_gb:.2f}x")
    print(f"   Largest files:")
    for filename, size in largest_files:
        size_mb = size / (1024**2)
        print(f"     {filename}: {size_mb:.1f} MB")
    print(f"📈 Throughput Analysis:")
    print(f"   Raw disk throughput: {disk_size_gb/total_load_time:.2f} GB/s")
    print(f"   Effective model throughput: {model_size_gb/total_load_time:.2f} GB/s")
    print(f"   Overhead factor: {(total_load_time/(disk_size_gb/3.0)):.2f}x slower than raw disk")
    
    result = {
        "model_name": model_name,
        "load_path": load_path,
        "storage_type": storage_type,
        "description": loading_desc,
        "load_time": total_load_time, # Store total load time
        "file_size_gb": model_size_gb,
        "load_speed_gbps": model_size_gb/total_load_time,
        "parameters": param_count,
        "parallel_workers": num_workers if use_parallel else 0,
        "use_parallel": use_parallel,
        "timestamp": time.time()
    }
    
    # Clean up to free memory
    del model
    del tokenizer
    torch.cuda.empty_cache() if torch.cuda.is_available() else None
    
    # Clean up environment variables
    os.environ.pop('HF_ENABLE_PARALLEL_LOADING', None)
    os.environ.pop('HF_PARALLEL_LOADING_WORKERS', None)
    
    # Evict the tested files from cache after benchmarking
    evict_files_from_cache(load_path)
    
    return result


def benchmark_four_configs(model_name, num_workers=8):
    """Benchmark 4 different configurations:
    1. S3 without parallel loading
    2. S3 with parallel loading  
    3. NVMe without parallel loading
    4. NVMe with parallel loading
    """
    print(f"\n=== Four-Configuration Benchmark for {model_name} ===")
    print("Testing: S3 (no parallel) → S3 (parallel) → NVMe (no parallel) → NVMe (parallel)")
    
    # Get model configuration from environment variables
    model_config = get_model_config_from_env()
    
    # Determine local paths
    local_dir = model_config.get(model_name, model_name.split('/')[-1])
    s3_path = f"/checkpoints_s3/{local_dir}"
    nvme_path = f"/tmp/checkpoints_nvme/{local_dir}"
    
    all_results = []
    
    # Ensure both S3 and NVMe copies exist
    if not os.path.exists(s3_path):
        print(f"❌ S3 path not found: {s3_path}")
        return []
    
    # Copy from S3 to NVMe if needed
    if not os.path.exists(nvme_path):
        print(f"📋 Copying {s3_path} to {nvme_path} for NVMe tests...")
        evict_files_from_cache(s3_path)
        copy_start = time.time()
        shutil.copytree(s3_path, nvme_path)
        copy_time = time.time() - copy_start
        print(f"✅ Copy completed in {copy_time:.2f}s")
    
    # Configuration 1: S3 without parallel loading
    print(f"\n--- Config 1: S3 Mount (No Parallel) ---")
    result1 = benchmark_single_location(model_name, s3_path, "S3 Mount", num_workers, use_parallel=False)
    if result1:
        all_results.append(result1)
    
    # Configuration 2: S3 with parallel loading
    print(f"\n--- Config 2: S3 Mount (Parallel) ---")
    result2 = benchmark_single_location(model_name, s3_path, "S3 Mount", num_workers, use_parallel=True)
    if result2:
        all_results.append(result2)
    
    # Configuration 3: NVMe without parallel loading
    print(f"\n--- Config 3: NVMe (No Parallel) ---")
    result3 = benchmark_single_location(model_name, nvme_path, "NVMe", num_workers, use_parallel=False)
    if result3:
        all_results.append(result3)
    
    # Configuration 4: NVMe with parallel loading
    print(f"\n--- Config 4: NVMe (Parallel) ---")
    result4 = benchmark_single_location(model_name, nvme_path, "NVMe", num_workers, use_parallel=True)
    if result4:
        all_results.append(result4)
    
    # Analyze results
    if len(all_results) == 4:
        print(f"\n=== Four-Configuration Performance Analysis for {model_name} ===")
        
        # Sort by load speed
        sorted_results = sorted(all_results, key=lambda x: x["load_speed_gbps"], reverse=True)
        
        print("🏆 Rankings by load speed:")
        for i, result in enumerate(sorted_results):
            rank_emoji = ["🥇", "🥈", "🥉", "4️⃣"][i]
            print(f"{rank_emoji} {result['description']}: {result['load_speed_gbps']:.2f} GB/s ({result['load_time']:.2f}s)")
        
        # Compare parallel vs non-parallel for each storage type
        s3_results = [r for r in all_results if r['storage_type'] == 'S3 Mount']
        nvme_results = [r for r in all_results if r['storage_type'] == 'NVMe']
        
        if len(s3_results) == 2:
            s3_parallel = next(r for r in s3_results if r['use_parallel'])
            s3_standard = next(r for r in s3_results if not r['use_parallel'])
            s3_speedup = s3_parallel['load_speed_gbps'] / s3_standard['load_speed_gbps']
            print(f"\n📊 S3 Mount Analysis:")
            print(f"   Standard: {s3_standard['load_speed_gbps']:.2f} GB/s")
            print(f"   Parallel: {s3_parallel['load_speed_gbps']:.2f} GB/s")
            print(f"   Speedup: {s3_speedup:.2f}x")
        
        if len(nvme_results) == 2:
            nvme_parallel = next(r for r in nvme_results if r['use_parallel'])
            nvme_standard = next(r for r in nvme_results if not r['use_parallel'])
            nvme_speedup = nvme_parallel['load_speed_gbps'] / nvme_standard['load_speed_gbps']
            print(f"\n💾 NVMe Analysis:")
            print(f"   Standard: {nvme_standard['load_speed_gbps']:.2f} GB/s")
            print(f"   Parallel: {nvme_parallel['load_speed_gbps']:.2f} GB/s")
            print(f"   Speedup: {nvme_speedup:.2f}x")
        
        # Compare storage types (using best performance from each)
        best_s3 = max(s3_results, key=lambda x: x['load_speed_gbps']) if s3_results else None
        best_nvme = max(nvme_results, key=lambda x: x['load_speed_gbps']) if nvme_results else None
        
        if best_s3 and best_nvme:
            storage_speedup = best_nvme['load_speed_gbps'] / best_s3['load_speed_gbps']
            print(f"\n🏁 Storage Comparison (best configs):")
            print(f"   Best S3: {best_s3['load_speed_gbps']:.2f} GB/s ({best_s3['description']})")
            print(f"   Best NVMe: {best_nvme['load_speed_gbps']:.2f} GB/s ({best_nvme['description']})")
            if storage_speedup > 1:
                print(f"   NVMe is {storage_speedup:.2f}x faster than S3")
            else:
                print(f"   S3 is {1/storage_speedup:.2f}x faster than NVMe")
    
    return all_results

def benchmark_six_configs(model_name, num_workers=8):
    """Benchmark 6 different configurations:
    1. S3 without parallel loading
    2. S3 with parallel loading  
    3. NVMe without parallel loading
    4. NVMe with parallel loading
    5. Nebius without parallel loading
    6. Nebius with parallel loading
    """
    print(f"\n=== Six-Configuration Benchmark for {model_name} ===")
    print("Testing: S3 (no parallel) → S3 (parallel) → NVMe (no parallel) → NVMe (parallel) → Nebius (no parallel) → Nebius (parallel)")
    
    # Get model configuration from environment variables
    model_config = get_model_config_from_env()
    
    # Determine local paths
    local_dir = model_config.get(model_name, model_name.split('/')[-1])
    s3_path = f"/checkpoints_s3/{local_dir}"
    nvme_path = f"/tmp/checkpoints_nvme/{local_dir}"
    nebius_path = f"/mnt/data/checkpoints/{local_dir}"
    
    all_results = []
    
    # Ensure S3 copy exists
    if not os.path.exists(s3_path):
        print(f"❌ S3 path not found: {s3_path}")
        return []
    
    # Copy from S3 to NVMe if needed
    if not os.path.exists(nvme_path):
        print(f"📋 Copying {s3_path} to {nvme_path} for NVMe tests...")
        evict_files_from_cache(s3_path)
        copy_start = time.time()
        shutil.copytree(s3_path, nvme_path)
        copy_time = time.time() - copy_start
        print(f"✅ NVMe copy completed in {copy_time:.2f}s")
    
    # Copy from S3 to Nebius if needed
    if not os.path.exists(nebius_path):
        print(f"📋 Copying {s3_path} to {nebius_path} for Nebius tests...")
        os.makedirs(os.path.dirname(nebius_path), exist_ok=True)
        evict_files_from_cache(s3_path)
        copy_start = time.time()
        shutil.copytree(s3_path, nebius_path)
        copy_time = time.time() - copy_start
        print(f"✅ Nebius copy completed in {copy_time:.2f}s")
    
    # Configuration 1: S3 without parallel loading
    print(f"\n--- Config 1: S3 Mount (No Parallel) ---")
    result1 = benchmark_single_location(model_name, s3_path, "S3 Mount", num_workers, use_parallel=False)
    if result1:
        all_results.append(result1)
    
    # Configuration 2: S3 with parallel loading
    print(f"\n--- Config 2: S3 Mount (Parallel) ---")
    result2 = benchmark_single_location(model_name, s3_path, "S3 Mount", num_workers, use_parallel=True)
    if result2:
        all_results.append(result2)
    
    # Configuration 3: NVMe without parallel loading
    print(f"\n--- Config 3: NVMe (No Parallel) ---")
    result3 = benchmark_single_location(model_name, nvme_path, "NVMe", num_workers, use_parallel=False)
    if result3:
        all_results.append(result3)
    
    # Configuration 4: NVMe with parallel loading
    print(f"\n--- Config 4: NVMe (Parallel) ---")
    result4 = benchmark_single_location(model_name, nvme_path, "NVMe", num_workers, use_parallel=True)
    if result4:
        all_results.append(result4)
    
    # Configuration 5: Nebius without parallel loading
    print(f"\n--- Config 5: Nebius (No Parallel) ---")
    result5 = benchmark_single_location(model_name, nebius_path, "Nebius", num_workers, use_parallel=False)
    if result5:
        all_results.append(result5)
    
    # Configuration 6: Nebius with parallel loading
    print(f"\n--- Config 6: Nebius (Parallel) ---")
    result6 = benchmark_single_location(model_name, nebius_path, "Nebius", num_workers, use_parallel=True)
    if result6:
        all_results.append(result6)
    
    # Analyze results
    if len(all_results) >= 4:
        print(f"\n=== Six-Configuration Performance Analysis for {model_name} ===")
        
        # Sort by load speed
        sorted_results = sorted(all_results, key=lambda x: x["load_speed_gbps"], reverse=True)
        
        print("🏆 Rankings by load speed:")
        rank_emojis = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"]
        for i, result in enumerate(sorted_results):
            rank_emoji = rank_emojis[i] if i < len(rank_emojis) else f"{i+1}."
            print(f"{rank_emoji} {result['description']}: {result['load_speed_gbps']:.2f} GB/s ({result['load_time']:.2f}s)")
        
        # Compare parallel vs non-parallel for each storage type
        s3_results = [r for r in all_results if r['storage_type'] == 'S3 Mount']
        nvme_results = [r for r in all_results if r['storage_type'] == 'NVMe']
        nebius_results = [r for r in all_results if r['storage_type'] == 'Nebius']
        
        if len(s3_results) == 2:
            s3_parallel = next(r for r in s3_results if r['use_parallel'])
            s3_standard = next(r for r in s3_results if not r['use_parallel'])
            s3_speedup = s3_parallel['load_speed_gbps'] / s3_standard['load_speed_gbps']
            print(f"\n📊 S3 Mount Analysis:")
            print(f"   Standard: {s3_standard['load_speed_gbps']:.2f} GB/s")
            print(f"   Parallel: {s3_parallel['load_speed_gbps']:.2f} GB/s")
            print(f"   Speedup: {s3_speedup:.2f}x")
        
        if len(nvme_results) == 2:
            nvme_parallel = next(r for r in nvme_results if r['use_parallel'])
            nvme_standard = next(r for r in nvme_results if not r['use_parallel'])
            nvme_speedup = nvme_parallel['load_speed_gbps'] / nvme_standard['load_speed_gbps']
            print(f"\n💾 NVMe Analysis:")
            print(f"   Standard: {nvme_standard['load_speed_gbps']:.2f} GB/s")
            print(f"   Parallel: {nvme_parallel['load_speed_gbps']:.2f} GB/s")
            print(f"   Speedup: {nvme_speedup:.2f}x")
        
        if len(nebius_results) == 2:
            nebius_parallel = next(r for r in nebius_results if r['use_parallel'])
            nebius_standard = next(r for r in nebius_results if not r['use_parallel'])
            nebius_speedup = nebius_parallel['load_speed_gbps'] / nebius_standard['load_speed_gbps']
            print(f"\n☁️ Nebius Analysis:")
            print(f"   Standard: {nebius_standard['load_speed_gbps']:.2f} GB/s")
            print(f"   Parallel: {nebius_parallel['load_speed_gbps']:.2f} GB/s")
            print(f"   Speedup: {nebius_speedup:.2f}x")
        
        # Compare storage types (using best performance from each)
        best_s3 = max(s3_results, key=lambda x: x['load_speed_gbps']) if s3_results else None
        best_nvme = max(nvme_results, key=lambda x: x['load_speed_gbps']) if nvme_results else None
        best_nebius = max(nebius_results, key=lambda x: x['load_speed_gbps']) if nebius_results else None
        
        print(f"\n🏁 Storage Comparison (best configs):")
        if best_s3:
            print(f"   Best S3: {best_s3['load_speed_gbps']:.2f} GB/s ({best_s3['description']})")
        if best_nvme:
            print(f"   Best NVMe: {best_nvme['load_speed_gbps']:.2f} GB/s ({best_nvme['description']})")
        if best_nebius:
            print(f"   Best Nebius: {best_nebius['load_speed_gbps']:.2f} GB/s ({best_nebius['description']})")
        
        # Calculate relative performance
        storage_results = [r for r in [best_s3, best_nvme, best_nebius] if r is not None]
        if len(storage_results) > 1:
            best_overall = max(storage_results, key=lambda x: x['load_speed_gbps'])
            print(f"\n🚀 Performance vs fastest:")
            for result in storage_results:
                if result != best_overall:
                    speedup = best_overall['load_speed_gbps'] / result['load_speed_gbps']
                    print(f"   {best_overall['storage_type']} is {speedup:.2f}x faster than {result['storage_type']}")
    
    return all_results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--s3_checkpoint_dir", default="/checkpoints_s3", help="S3 checkpoint directory")
    parser.add_argument("--nvme_checkpoint_dir", default="/tmp/checkpoints_nvme", help="NVMe checkpoint directory")
    parser.add_argument("--hf_parallel_workers", type=int, default=8, help="Number of workers for HF parallel loading")
    parser.add_argument("--multi_model_benchmark", action="store_true", help="Run multi-model benchmarking")
    parser.add_argument("--benchmark_only", action="store_true", help="Only run benchmarking on existing models")
    parser.add_argument("--four_config_benchmark", action="store_true", help="Run 4-configuration benchmark (S3/NVMe × parallel/standard) instead of default 6-config")
    parser.add_argument("--six_config_benchmark", action="store_true", help="Run 6-configuration benchmark (S3/NVMe/Nebius × parallel/standard) - this is the default")
    parser.add_argument("--test_models", nargs="*", help="Models to benchmark (if not provided, will use TEST_MODELS env var)")
    args = parser.parse_args()
    
    # Get test models from environment variable if not provided via command line
    if args.test_models:
        test_models = args.test_models
    else:
        test_models_env = os.environ.get('TEST_MODELS', '')
        if test_models_env:
            test_models = [m.strip() for m in test_models_env.split(',')]
        else:
            # Fallback to default models
            test_models = [
                "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
                "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B", 
                "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
                "deepseek-ai/DeepSeek-R1-Distill-Llama-70B"
            ]
    
    # Create directories
    os.makedirs(args.s3_checkpoint_dir, exist_ok=True)
    os.makedirs(args.nvme_checkpoint_dir, exist_ok=True)
    os.makedirs("/mnt/data/checkpoints", exist_ok=True)
    
    # Four-configuration benchmarking mode (legacy)
    if args.four_config_benchmark:
        print("=== Four-Configuration Benchmark Mode (Legacy) ===")
        print("Testing: S3 (no parallel) → S3 (parallel) → NVMe (no parallel) → NVMe (parallel)")
        print(f"Models to test: {test_models}")
        
        all_four_config_results = []
        
        for model_name in test_models:
            print(f"\n{'='*60}")
            print(f"FOUR-CONFIG BENCHMARK: {model_name}")
            print(f"{'='*60}")
            
            # Run the four-configuration benchmark for this model
            model_results = benchmark_four_configs(model_name, args.hf_parallel_workers)
            
            if model_results:
                four_config_result = {
                    "model_name": model_name,
                    "configurations": model_results,
                    "timestamp": time.time()
                }
                all_four_config_results.append(four_config_result)
        
        # Save results
        with open("/tmp/four_config_benchmark_results.json", "w") as f:
            json.dump(all_four_config_results, f, indent=2)
        
        # Print overall summary
        print(f"\n{'='*60}")
        print("FOUR-CONFIG BENCHMARK OVERALL SUMMARY")
        print(f"{'='*60}")
        
        for result in all_four_config_results:
            model_name = result["model_name"]
            configs = result["configurations"]
            
            if len(configs) == 4:
                print(f"\n{model_name}:")
                sorted_configs = sorted(configs, key=lambda x: x["load_speed_gbps"], reverse=True)
                
                for i, config in enumerate(sorted_configs):
                    rank_emoji = ["🥇", "🥈", "🥉", "4️⃣"][i]
                    print(f"  {rank_emoji} {config['description']}: {config['load_speed_gbps']:.2f} GB/s")
        
        print(f"\nDetailed results saved to: /tmp/four_config_benchmark_results.json")

    # Six-configuration benchmarking mode (DEFAULT)
    else:
        print("=== Six-Configuration Benchmark Mode (Default) ===")
        print("Testing: S3 (no parallel) → S3 (parallel) → NVMe (no parallel) → NVMe (parallel) → Nebius (no parallel) → Nebius (parallel)")
        print(f"Models to test: {test_models}")
        
        all_six_config_results = []
        
        for model_name in test_models:
            print(f"\n{'='*60}")
            print(f"SIX-CONFIG BENCHMARK: {model_name}")
            print(f"{'='*60}")
            
            # Run the six-configuration benchmark for this model
            model_results = benchmark_six_configs(model_name, args.hf_parallel_workers)
            
            if model_results:
                six_config_result = {
                    "model_name": model_name,
                    "configurations": model_results,
                    "timestamp": time.time()
                }
                all_six_config_results.append(six_config_result)
        
        # Save results
        with open("/tmp/six_config_benchmark_results.json", "w") as f:
            json.dump(all_six_config_results, f, indent=2)
        
        # Print overall summary
        print(f"\n{'='*60}")
        print("SIX-CONFIG BENCHMARK OVERALL SUMMARY")
        print(f"{'='*60}")
        
        for result in all_six_config_results:
            model_name = result["model_name"]
            configs = result["configurations"]
            
            if len(configs) >= 4: # At least 4 results for 6 configs
                print(f"\n{model_name}:")
                sorted_configs = sorted(configs, key=lambda x: x["load_speed_gbps"], reverse=True)
                
                for i, config in enumerate(sorted_configs):
                    rank_emoji = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"][i] if i < 6 else f"{i+1}."
                    print(f"  {rank_emoji} {config['description']}: {config['load_speed_gbps']:.2f} GB/s")
        
        print(f"\nDetailed results saved to: /tmp/six_config_benchmark_results.json")

if __name__ == "__main__":
    main()
