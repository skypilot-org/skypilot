# ?? Styx Examples

Rust examples showing how to use Styx for various tasks.

---

## ?? **Available Examples**

| Example | Description | Complexity |
|---------|-------------|------------|
| `hello_world.rs` | Simplest example - submit one task | ? Easy |
| `multi_task.rs` | Submit multiple tasks with priorities | ? Easy |
| `task_dag.rs` | Tasks with dependencies (DAG) | ?? Medium |
| `cloud_provision.rs` | Provision cloud instances | ?? Medium |
| `gpu_task.rs` | Task requiring GPU resources | ?? Medium |
| `batch_jobs.rs` | Submit many tasks (parameter sweep) | ?? Medium |
| `distributed_training.rs` | Multi-node distributed training | ??? Advanced |

---

## ?? **Running Examples**

### **Run individual example:**
```bash
cargo run --example hello_world
cargo run --example multi_task
cargo run --example task_dag
```

### **Run all examples:**
```bash
cargo run --examples
```

---

## ?? **Example Details**

### **1. hello_world.rs**
The simplest possible example:
```rust
let scheduler = Scheduler::new(SchedulerConfig::default());
let task = Task::new("hello", "echo").with_arg("Hello!".to_string());
let id = scheduler.submit(task).await?;
```

**Output:**
```
? Task submitted: abc123...
Task will execute: echo 'Hello from Styx!'
```

---

### **2. multi_task.rs**
Submit multiple tasks with different priorities:
```rust
let task1 = Task::new("task-1", "python")
    .with_priority(TaskPriority::High);
let task2 = Task::new("task-2", "python")
    .with_priority(TaskPriority::Normal);
```

**Output:**
```
? Submitted 3 tasks:
  High priority:   abc123...
  Normal priority: def456...
  Low priority:    ghi789...
```

---

### **3. task_dag.rs**
Create task dependencies:
```rust
let id1 = scheduler.submit(task1).await?;
let task2 = Task::new("task2", "cmd").with_dependency(id1);
let id2 = scheduler.submit(task2).await?;
```

**Output:**
```
? Submitted DAG with 4 tasks:
  1. download-data  -> abc...
  2. preprocess     -> def... (depends on 1)
  3. train          -> ghi... (depends on 2)
  4. evaluate       -> jkl... (depends on 3)

Execution order will be: 1 ? 2 ? 3 ? 4
```

---

### **4. cloud_provision.rs**
Provision instances on different clouds:
```rust
let aws = AwsProvider::new().await?;
let request = ProvisionRequest::new("my-instance", resources);
let instance = aws.provision(request).await?;
```

**Output:**
```
?? Cloud Provisioning Example

1?? AWS Provisioning:
  ? AWS Instance: i-abc123 (t3.xlarge)

2?? GCP Provisioning:
  ? GCP Instance: gcp-def456 (n1-standard-4)

3?? Kubernetes Provisioning:
  ? Kubernetes Pod: pod-ghi789 (my-pod)
```

---

### **5. gpu_task.rs**
Submit task with GPU requirements:
```rust
task.resources = ResourceRequirements::new()
    .with_cpu(8.0)
    .with_memory(32.0)
    .with_gpu(2)
    .with_gpu_type("nvidia-v100");
```

**Output:**
```
? GPU Task submitted: abc123...

?? Task details:
  Command: python train_llm.py --model gpt2
  CPU: 8 cores
  Memory: 32 GB
  GPU: 2x nvidia-v100
  Priority: Critical
```

---

### **6. batch_jobs.rs**
Hyperparameter sweep:
```rust
for lr in &[0.001, 0.01, 0.1] {
    for batch_size in &[32, 64, 128] {
        let task = Task::new(&format!("train-lr{}-bs{}", lr, batch_size), "python")
            .with_arg("--lr".to_string())
            .with_arg(lr.to_string());
        scheduler.submit(task).await?;
    }
}
```

**Output:**
```
?? Submitting batch jobs...

? Submitted 16 tasks:
  train-lr0.001-bs32 -> abc...
  train-lr0.001-bs64 -> def...
  train-lr0.001-bs128 -> ghi...
  ... and 13 more
```

---

### **7. distributed_training.rs**
Multi-node training:
```rust
// Master task
let master = Task::new("master", "python")
    .with_arg("--role".to_string())
    .with_arg("master".to_string());

// Worker tasks (depend on master)
for i in 0..num_workers {
    let worker = Task::new(&format!("worker-{}", i), "python")
        .with_dependency(master_id)
        .with_gpu(1);
    scheduler.submit(worker).await?;
}
```

**Output:**
```
?? Distributed Training Example

? Master task: abc123...
? Worker 0 task: def456...
? Worker 1 task: ghi789...
? Worker 2 task: jkl012...
? Worker 3 task: mno345...

?? Distributed Setup:
  1 Master + 4 Workers
  Total GPUs: 4
  Workers wait for master to start
```

---

## ?? **Learning Path**

**Beginners:**
1. Start with `hello_world.rs`
2. Try `multi_task.rs` 
3. Learn dependencies with `task_dag.rs`

**Intermediate:**
1. Understand cloud provisioning: `cloud_provision.rs`
2. Work with GPUs: `gpu_task.rs`
3. Scale with batch jobs: `batch_jobs.rs`

**Advanced:**
1. Multi-node training: `distributed_training.rs`

---

## ?? **Common Patterns**

### **Submit and Wait**
```rust
let task_id = scheduler.submit(task).await?;
let task = scheduler.get_task(task_id).await;
while task.status != TaskStatus::Completed {
    tokio::time::sleep(Duration::from_secs(1)).await;
    task = scheduler.get_task(task_id).await;
}
```

### **Error Handling**
```rust
match scheduler.submit(task).await {
    Ok(id) => println!("Success: {}", id),
    Err(e) => eprintln!("Error: {}", e),
}
```

### **Resource Requirements**
```rust
let resources = ResourceRequirements::new()
    .with_cpu(4.0)
    .with_memory(16.0)
    .with_gpu(1);
task.resources = resources;
```

---

## ?? **See Also**

- [Main README](../README.md)
- [API Documentation](../STYX_COMPLETE_OVERVIEW.md)
- [Deployment Guide](../DEPLOYMENT.md)

---

**?? Happy coding with Styx!** ??
