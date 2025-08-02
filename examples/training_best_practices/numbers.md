(task, pid=2552)    Evicted Pages: 3720495 (14G)
(task, pid=2552)          Elapsed: 0.65636 seconds
(task, pid=2552)
(task, pid=2552) === Four-Configuration Performance Analysis for deepseek-ai/DeepSeek-R1-Distill-Qwen-7B ===
(task, pid=2552) 🏆 Rankings by load speed:
(task, pid=2552) 🥇 NVMe (Standard): 2.25 GB/s (11.72s)
(task, pid=2552) 🥈 NVMe (Parallel): 1.37 GB/s (19.21s)
(task, pid=2552) 🥉 S3 Mount (Standard): 0.19 GB/s (142.22s)
(task, pid=2552) 4️⃣ S3 Mount (Parallel): 0.16 GB/s (164.68s)
(task, pid=2552)
(task, pid=2552) 📊 S3 Mount Analysis:
(task, pid=2552)    Standard: 0.19 GB/s
(task, pid=2552)    Parallel: 0.16 GB/s
(task, pid=2552)    Speedup: 0.86x
(task, pid=2552)
(task, pid=2552) 💾 NVMe Analysis:
(task, pid=2552)    Standard: 2.25 GB/s
(task, pid=2552)    Parallel: 1.37 GB/s
(task, pid=2552)    Speedup: 0.61x
(task, pid=2552)
(task, pid=2552) 🏁 Storage Comparison (best configs):
(task, pid=2552)    Best S3: 0.19 GB/s (S3 Mount (Standard))
(task, pid=2552)    Best NVMe: 2.25 GB/s (NVMe (Standard))
(task, pid=2552)    NVMe is 12.14x faster than S3




(task, pid=2567) deepseek-ai/DeepSeek-R1-Distill-Qwen-7B:
(task, pid=2567)   🥇 NVMe (Standard): 2.19 GB/s
(task, pid=2567)   🥈 NVMe (Parallel): 1.34 GB/s
(task, pid=2567)   🥉 S3 Mount (Parallel): 0.18 GB/s
(task, pid=2567)   4️⃣ S3 Mount (Standard): 0.14 GB/s



FIO


SAVING
(task, pid=2662) Results saved to: /tmp/save_benchmark_results.json
(task, pid=2662)
(task, pid=2662) ================================================================================
(task, pid=2662) COMPREHENSIVE CHECKPOINT SAVING ANALYSIS
(task, pid=2662) ================================================================================
(task, pid=2662)
(task, pid=2662) ============================================================
(task, pid=2662) MODEL: deepseek-ai/DeepSeek-R1-Distill-Qwen-7B
(task, pid=2662) Size: 14.19 GB, Parameters: 7,615,616,512
(task, pid=2662) ============================================================
(task, pid=2662) Rank  Backend + Format                         Avg Speed (GB/s)   Avg Time (s)    File Size (GB)
(task, pid=2662) -----------------------------------------------------------------------------------------------
(task, pid=2662) 🥇     nvme (save_pretrained)                   0.49               28.80           14.20
(task, pid=2662) 🥈     s3_mount_cached (save_pretrained)        0.37               38.79           14.20
(task, pid=2662) 🥉     s3_mount (save_pretrained)               0.34               41.65           14.20



DATASET ITER/s



END TO END

