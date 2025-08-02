#!/usr/bin/env python3
"""
DeepSeek Distilled Models Checkpoint Saving Benchmark.
Compares saving performance across NVMe, S3 mounted, and S3 direct storage backends.
"""

import argparse
import gc
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback

import boto3
import psutil
from safetensors.torch import save_file as save_safetensors
import torch
from transformers import AutoConfig
from transformers import AutoModelForCausalLM
from transformers import AutoTokenizer


def clear_system_caches():
    """Clear system caches using sync and vmtouch for fair benchmarking"""
    print("🧹 Flushing caches...")
    
    try:
        # 1. Sync filesystem buffers
        subprocess.run(['sync'], check=True, capture_output=True)
        
        # 2. Drop page caches
        try:
            subprocess.run(['sudo', 'sh', '-c', 'echo 3 > /proc/sys/vm/drop_caches'], 
                         check=True, capture_output=True, timeout=10)
        except:
            pass
        
        # 3. Evict specific directories with vmtouch
        cache_dirs = [
            '/tmp/checkpoints_nvme',
            '/checkpoints_s3_mount',
            '/checkpoints_s3_mount_cached',
            '/mnt/data/checkpoints_save'
        ]
        
        for cache_dir in cache_dirs:
            if os.path.exists(cache_dir):
                try:
                    subprocess.run(['vmtouch', '-e', '-r', cache_dir], 
                                 check=True, capture_output=True, timeout=30)
                except:
                    pass
        
        # 4. Python cleanup
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            
        print("  ✅ Cache flush complete")
        
    except Exception as e:
        print(f"  ⚠️ Cache flush failed: {e}")


def get_cache_usage_info():
    """Get information about current cache usage"""
    try:
        # Get memory info
        memory = psutil.virtual_memory()
        
        cache_info = {
            "memory_used_gb": memory.used / (1024**3),
            "memory_available_gb": memory.available / (1024**3)
        }
        
        # Get basic cache info from /proc/meminfo if available
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'Cached:' in line:
                        cached_kb = int(line.split()[1])
                        cache_info["page_cache_gb"] = cached_kb / (1024**2)
                        break
        except:
            pass
            
        return cache_info
    except:
        return {"memory_used_gb": 0}


def get_memory_usage():
    """Get current system memory usage"""
    memory = psutil.virtual_memory()
    return {
        "total_gb": memory.total / (1024**3),
        "available_gb": memory.available / (1024**3),
        "used_gb": memory.used / (1024**3),
        "used_percent": memory.percent
    }


def get_disk_usage(path):
    """Get disk usage for a path"""
    if os.path.exists(path):
        usage = shutil.disk_usage(path)
        return {
            "total_gb": usage.total / (1024**3),
            "free_gb": usage.free / (1024**3),
            "used_gb": usage.used / (1024**3)
        }
    return {}


def load_model_for_checkpoint(model_name, model_path):
    """Load a model for checkpoint saving benchmark"""
    print(f"Loading model {model_name} from {model_path}")
    
    start_time = time.time()
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            model_path, 
            trust_remote_code=True
        )
        
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            trust_remote_code=True,
            torch_dtype=torch.float16,
            device_map="cpu",  # Load to CPU first for saving benchmarks
            low_cpu_mem_usage=True
        )
        
        load_time = time.time() - start_time
        
        # Get model information
        param_count = sum(p.numel() for p in model.parameters())
        model_size_gb = param_count * 2 / (1024**3)  # float16 = 2 bytes per param
        
        model_info = {
            "model_name": model_name,
            "parameters": param_count,
            "model_size_gb": model_size_gb,
            "load_time": load_time,
            "vocab_size": tokenizer.vocab_size if hasattr(tokenizer, 'vocab_size') else None,
            "model_type": model.config.model_type if hasattr(model, 'config') else None
        }
        
        print(f"✅ Loaded {model_name}: {param_count:,} params, {model_size_gb:.2f} GB")
        return model, tokenizer, model_info
        
    except Exception as e:
        print(f"❌ Failed to load {model_name}: {e}")
        return None, None, None


def benchmark_model_save_pretrained(model, tokenizer, save_path, model_info):
    """Benchmark model save using save_pretrained method"""
    print(f"Saving model with save_pretrained to {save_path}")
    
    memory_before = get_memory_usage()
    start_time = time.time()
    
    try:
        # Create directory
        os.makedirs(save_path, exist_ok=True)
        
        # Save model and tokenizer using the standard method
        model.save_pretrained(save_path)
        tokenizer.save_pretrained(save_path)
        
        save_time = time.time() - start_time
        memory_after = get_memory_usage()
        
        # Calculate total size of saved model
        total_size_bytes = 0
        for root, dirs, files in os.walk(save_path):
            for file in files:
                file_path = os.path.join(root, file)
                total_size_bytes += os.path.getsize(file_path)
        
        file_size_gb = total_size_bytes / (1024**3)
        save_speed_gbps = file_size_gb / save_time if save_time > 0 else 0
        
        result = {
            "save_path": save_path,
            "save_format": "save_pretrained",
            "save_time": save_time,
            "file_size_gb": file_size_gb,
            "save_speed_gbps": save_speed_gbps,
            "model_size_gb": model_info["model_size_gb"],
            "memory_delta_gb": memory_after["used_gb"] - memory_before["used_gb"],
            "timestamp": time.time()
        }
        
        print(f"✅ save_pretrained: {save_time:.2f}s, {file_size_gb:.2f} GB, {save_speed_gbps:.2f} GB/s")
        return result
        
    except Exception as e:
        print(f"❌ save_pretrained failed: {e}")
        return {"error": str(e), "save_format": "save_pretrained"}


def benchmark_model_saving(model_name, model_path, storage_configs, iterations=1):
    """Benchmark saving a model across different storage backends"""
    print(f"\n{'='*60}")
    print(f"BENCHMARKING MODEL SAVING: {model_name}")
    print(f"{'='*60}")
    
    # Load the model from NVMe (fast local storage)
    model, tokenizer, model_info = load_model_for_checkpoint(model_name, model_path)
    if not model:
        return None
    
    all_results = []
    
    try:
        for storage_name, storage_config in storage_configs.items():
            print(f"\n--- Testing Storage Backend: {storage_name} ---")
            print(f"Storage path: {storage_config['path']}")
            print(f"Description: {storage_config['description']}")
            
            # Clear caches before testing this storage backend
            clear_system_caches()
            
            storage_results = []
            
            for iteration in range(iterations):
                print(f"\nIteration {iteration + 1}/{iterations}")
                
                # Clear caches before each iteration for consistent results
                if iteration > 0:
                    clear_system_caches()
                
                # Generate unique save directory
                save_dir = os.path.join(storage_config["path"], f"{model_name.split('/')[-1]}_iter{iteration+1}")
                
                # Save using standard save_pretrained method
                result = benchmark_model_save_pretrained(model, tokenizer, save_dir, model_info)
                
                if result and "error" not in result:
                    result.update({
                        "storage_backend": storage_name,
                        "storage_config": storage_config,
                        "iteration": iteration + 1,
                        "model_name": model_name,
                        "cache_cleared": True
                    })
                    storage_results.append(result)
            
            all_results.extend(storage_results)
        
        # Clean up model
        del model
        del tokenizer
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        
        # Final cache clear after model cleanup
        clear_system_caches()
        
        # Calculate averages per storage backend and format
        summary_results = analyze_storage_performance(all_results, model_info)
        
        return {
            "model_name": model_name,
            "model_info": model_info,
            "detailed_results": all_results,
            "summary": summary_results,
            "timestamp": time.time()
        }
        
    except Exception as e:
        print(f"❌ Error during benchmark: {e}")
        traceback.print_exc()
        return None


def analyze_storage_performance(results, model_info):
    """Analyze and summarize storage performance results"""
    if not results:
        return {}
    
    # Group by storage backend and format
    grouped = {}
    for result in results:
        if "error" in result:
            continue
            
        key = f"{result['storage_backend']}_{result.get('save_format', 'unknown')}"
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(result)
    
    summary = {}
    for key, group_results in grouped.items():
        save_times = [r.get("save_time", 0) for r in group_results if "save_time" in r]
        save_speeds = [r.get("save_speed_gbps", 0) for r in group_results if "save_speed_gbps" in r]
        upload_speeds = [r.get("upload_speed_gbps", 0) for r in group_results if "upload_speed_gbps" in r]
        file_sizes = [r.get("file_size_gb", 0) for r in group_results if "file_size_gb" in r]
        
        if save_times or upload_speeds:
            all_speeds = save_speeds + upload_speeds
            all_times = save_times + [r.get("upload_time", 0) for r in group_results if "upload_time" in r]
            
            summary[key] = {
                "backend": group_results[0]["storage_backend"],
                "format": group_results[0].get("save_format", "unknown"),
                "iterations": len(group_results),
                "avg_time": sum(all_times) / len(all_times) if all_times else 0,
                "avg_speed_gbps": sum(all_speeds) / len(all_speeds) if all_speeds else 0,
                "min_time": min(all_times) if all_times else 0,
                "max_time": max(all_times) if all_times else 0,
                "avg_file_size_gb": sum(file_sizes) / len(file_sizes) if file_sizes else 0,
                "compression_ratio": model_info["model_size_gb"] / (sum(file_sizes) / len(file_sizes)) if file_sizes else 0
            }
    
    return summary


def analyze_comprehensive_results(results_file):
    """Analyze comprehensive benchmark results"""
    with open(results_file, 'r') as f:
        all_results = json.load(f)
    
    print("\n" + "="*80)
    print("COMPREHENSIVE CHECKPOINT SAVING ANALYSIS")
    print("="*80)
    
    # Group by model and storage backend
    model_summaries = {}
    for model_result in all_results:
        if not model_result or "summary" not in model_result:
            continue
            
        model_name = model_result["model_name"]
        summary = model_result["summary"]
        model_info = model_result["model_info"]
        
        if model_name not in model_summaries:
            model_summaries[model_name] = {
                "model_info": model_info,
                "backends": summary
            }
    
    # Print results by model
    for model_name, data in model_summaries.items():
        print(f"\n{'='*60}")
        print(f"MODEL: {model_name}")
        print(f"Size: {data['model_info']['model_size_gb']:.2f} GB, Parameters: {data['model_info']['parameters']:,}")
        print(f"{'='*60}")
        
        # Sort backends by average speed
        backends = data["backends"]
        sorted_backends = sorted(backends.items(), key=lambda x: x[1]["avg_speed_gbps"], reverse=True)
        
        print(f"{'Rank':<5} {'Backend + Format':<40} {'Avg Speed (GB/s)':<18} {'Avg Time (s)':<15} {'File Size (GB)':<15}")
        print("-" * 95)
        
        for i, (backend_key, stats) in enumerate(sorted_backends):
            rank_emoji = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i+1:2d}."
            backend_name = f"{stats['backend']} ({stats['format']})"
            print(f"{rank_emoji:<5} {backend_name:<40} {stats['avg_speed_gbps']:<18.2f} {stats['avg_time']:<15.2f} {stats['avg_file_size_gb']:<15.2f}")
    
    # Cross-model analysis
    print(f"\n{'='*60}")
    print("CROSS-MODEL STORAGE BACKEND ANALYSIS")
    print(f"{'='*60}")
    
    # Aggregate performance by storage backend across all models
    backend_aggregates = {}
    for model_name, data in model_summaries.items():
        for backend_key, stats in data["backends"].items():
            backend = stats["backend"]
            if backend not in backend_aggregates:
                backend_aggregates[backend] = {
                    "speeds": [],
                    "times": [],
                    "file_sizes": [],
                    "models": []
                }
            
            backend_aggregates[backend]["speeds"].append(stats["avg_speed_gbps"])
            backend_aggregates[backend]["times"].append(stats["avg_time"])
            backend_aggregates[backend]["file_sizes"].append(stats["avg_file_size_gb"])
            backend_aggregates[backend]["models"].append(model_name)
    
    print(f"{'Backend':<30} {'Avg Speed (GB/s)':<18} {'Models Tested':<15}")
    print("-" * 65)
    
    sorted_backends = sorted(backend_aggregates.items(), 
                           key=lambda x: sum(x[1]["speeds"]) / len(x[1]["speeds"]), 
                           reverse=True)
    
    for backend, stats in sorted_backends:
        avg_speed = sum(stats["speeds"]) / len(stats["speeds"])
        model_count = len(set(stats["models"]))
        print(f"{backend:<30} {avg_speed:<18.2f} {model_count:<15}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test_models", required=True, help="Comma-separated list of models to test")
    parser.add_argument("--local_model_dirs", required=True, help="Comma-separated list of local model directories")
    parser.add_argument("--nvme_checkpoint_dir", default="/tmp/checkpoints_nvme", help="NVMe checkpoint directory")
    parser.add_argument("--s3_mount_dir", default="/checkpoints_s3_mount", help="S3 MOUNT directory")
    parser.add_argument("--s3_mount_cached_dir", default="/checkpoints_s3_mount_cached", help="S3 MOUNT_CACHED directory")
    parser.add_argument("--nebius_dir", default="/mnt/data/checkpoints_save", help="Nebius shared filesystem directory")
    parser.add_argument("--s3_mounted_dir", default=None, help="Legacy S3 mounted directory (deprecated)")
    parser.add_argument("--save_iterations", type=int, default=1, help="Number of save iterations per test")
    parser.add_argument("--output_results", default="/tmp/save_benchmark_results.json", help="Output results file")
    parser.add_argument("--analyze_results_only", action="store_true", help="Only analyze existing results")
    parser.add_argument("--results_file", help="Results file to analyze")
    parser.add_argument("--generate_detailed_report", action="store_true", help="Generate detailed report")
    args = parser.parse_args()
    
    if args.analyze_results_only:
        if args.results_file and os.path.exists(args.results_file):
            analyze_comprehensive_results(args.results_file)
        else:
            print("Results file not found or not specified")
        return
    
    # Parse input arguments
    test_models = [model.strip() for model in args.test_models.split(",")]
    local_model_dirs = [dir.strip() for dir in args.local_model_dirs.split(",")]
    
    if len(test_models) != len(local_model_dirs):
        print("Error: Number of test models and local model directories must match")
        return
    
    # Support backward compatibility with old argument name
    if args.s3_mounted_dir and not args.s3_mount_cached_dir:
        args.s3_mount_cached_dir = args.s3_mounted_dir
        print(f"Using legacy s3_mounted_dir as s3_mount_cached_dir: {args.s3_mount_cached_dir}")
    
    # Configure storage backends for comprehensive comparison
    storage_configs = {
        "nvme": {
            "path": args.nvme_checkpoint_dir,
            "description": "NVMe Local Storage"
        },
        "s3_mount": {
            "path": args.s3_mount_dir,
            "description": "S3 FUSE Mounted Storage (MOUNT mode)"
        },
        "s3_mount_cached": {
            "path": args.s3_mount_cached_dir,
            "description": "S3 FUSE Mounted Storage (MOUNT_CACHED mode)"
        },
        "nebius": {
            "path": args.nebius_dir,
            "description": "Nebius Shared Filesystem (Distributed storage)"
        }
    }
    
    print(f"=== Model Checkpoint Saving Benchmark ===")
    print(f"Models to test: {test_models}")
    print(f"Storage backends: {list(storage_configs.keys())}")
    print(f"Iterations per test: {args.save_iterations}")
    print(f"\nAlways loading models from NVMe for consistent baseline")
    print(f"Mount Mode Comparison:")
    print(f"  - S3 MOUNT: {args.s3_mount_dir}")
    print(f"  - S3 MOUNT_CACHED: {args.s3_mount_cached_dir}")
    
    # Create directories
    for config in storage_configs.values():
        os.makedirs(config["path"], exist_ok=True)
    
    all_model_results = []
    
    # Test each model - always load from NVMe
    for model_name, local_dir in zip(test_models, local_model_dirs):
        # Always load from NVMe for consistent baseline
        nvme_model_path = os.path.join(args.nvme_checkpoint_dir, local_dir)
        
        if not os.path.exists(nvme_model_path):
            print(f"⚠️ Model not found in NVMe: {nvme_model_path}, skipping {model_name}")
            continue
        
        print(f"📂 Loading model {model_name} from NVMe: {nvme_model_path}")
        
        result = benchmark_model_saving(
            model_name=model_name,
            model_path=nvme_model_path,
            storage_configs=storage_configs,
            iterations=args.save_iterations
        )
        
        if result:
            result["model_source"] = "NVMe"
            all_model_results.append(result)
    
    # Save results
    with open(args.output_results, 'w') as f:
        json.dump(all_model_results, f, indent=2)
    
    print(f"\nResults saved to: {args.output_results}")
    
    # Generate analysis
    if all_model_results:
        analyze_comprehensive_results(args.output_results)
        
        print(f"\n=== MOUNT vs MOUNT_CACHED vs NEBIUS COMPARISON ===")
        # Specific analysis for MOUNT vs MOUNT_CACHED vs Nebius
        mount_results = {}
        for model_result in all_model_results:
            if "summary" not in model_result:
                continue
            model_name = model_result["model_name"]
            summary = model_result["summary"]
            
            for backend_key, stats in summary.items():
                backend = stats["backend"]
                if backend in ["s3_mount", "s3_mount_cached", "nebius"]:
                    if backend == "s3_mount":
                        mount_mode = "S3_MOUNT"
                    elif backend == "s3_mount_cached":
                        mount_mode = "S3_MOUNT_CACHED"
                    else:
                        mount_mode = "NEBIUS"
                    
                    format_name = stats["format"]
                    
                    key = f"{mount_mode}_{format_name}"
                    if key not in mount_results:
                        mount_results[key] = []
                    mount_results[key].append({
                        "model": model_name,
                        "speed": stats["avg_speed_gbps"],
                        "time": stats["avg_time"],
                        "size": stats["avg_file_size_gb"]
                    })
        
        print(f"\n{'Storage Backend':<25} {'Format':<15} {'Avg Speed (GB/s)':<18} {'Avg Time (s)':<15}")
        print("-" * 75)
        
        for key, results in sorted(mount_results.items()):
            storage_backend, format_name = key.split("_", 1)
            avg_speed = sum(r["speed"] for r in results) / len(results)
            avg_time = sum(r["time"] for r in results) / len(results)
            print(f"{storage_backend:<25} {format_name:<15} {avg_speed:<18.2f} {avg_time:<15.2f}")
        
        print(f"\n=== FINAL SUMMARY ===")
        print(f"Successfully benchmarked {len(all_model_results)} models")
        print(f"Total storage backends tested: {len(storage_configs)}")
        print(f"Total checkpoint formats tested: 1 (save_pretrained)")
        print(f"Storage backends compared: NVMe, S3 MOUNT, S3 MOUNT_CACHED, Nebius")


if __name__ == "__main__":
    main() 