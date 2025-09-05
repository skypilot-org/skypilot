# 内存追踪脚本使用指南

本目录包含两个内存追踪脚本，用于分析程序的内存分配和释放模式：

## 1. malloc.bat (bpftrace 版本)

### 适用系统
- 主要适用于 Linux 系统
- 在 macOS 上需要特殊配置

### 使用方法
```bash
# 安装 bpftrace (如果尚未安装)
# Ubuntu/Debian: sudo apt install bpftrace
# CentOS/RHEL: sudo yum install bpftrace
# macOS: brew install bpftrace

# 运行脚本
sudo bpftrace -p <PID> malloc.bat
```

### 功能特性
- 追踪 malloc/free/calloc/realloc/mmap/munmap
- 按调用栈统计分配和释放字节数
- 每 10 秒输出统计信息
- 支持采样以降低性能开销

## 2. malloc_macos.d (dtrace 版本)

### 适用系统
- 专为 macOS 设计
- 使用系统内置的 dtrace

### 使用方法
```bash
# 直接运行
sudo dtrace -s malloc_macos.d -p <PID>

# 或者使脚本可执行后运行
sudo ./malloc_macos.d -p <PID>
```

### 功能特性
- 追踪 malloc/free/calloc/realloc
- 统计分配字节数和调用次数
- 显示调用栈信息
- 每 10 秒输出统计

## 故障排除

### 常见错误

1. **"uprobe target file 'libc' does not exist"**
   - 修改 `malloc.bat` 中的 `LIBC` 定义
   - Linux: 使用完整路径，如 `"/lib/x86_64-linux-gnu/libc.so.6"`
   - 查找正确路径：`ldd /bin/ls | grep libc`

2. **权限错误**
   - 必须使用 `sudo` 运行
   - 确保目标进程存在且有权限访问

3. **macOS 上 bpftrace 不工作**
   - 使用 `malloc_macos.d` 替代
   - 或者禁用 SIP (不推荐)

### 查找目标进程 PID
```bash
# 按进程名查找
pgrep python
ps aux | grep python

# 按端口查找
lsof -i :8080
```

## 输出解读

### 分配统计 (Allocation Sites)
- 显示内存分配最多的调用栈
- 关注字节数较大的位置

### 释放统计 (Free Sites)
- 显示内存释放的调用栈
- 与分配统计对比，找出可能的泄漏

### 内存泄漏识别
- 分配字节数很高，但释放字节数很低的调用栈
- mmap 明显高于 munmap 的调用栈
- 长时间运行后总分配量持续增长

## 性能考虑

- 使用采样 (修改 `SAMPLE` 值) 来降低开销
- 在生产环境中谨慎使用
- 监控期间可能会影响程序性能

## 示例输出

```
================== 14:30:25 ==================
Top allocation sites (malloc family) - BYTES allocated:
@by_alloc_stack_bytes[
    python3+0x1234
    libc.so.6+0x5678
    ...
]: 1048576

Top free sites - BYTES freed:
@by_free_stack_bytes[
    python3+0x1234
    libc.so.6+0x5678
    ...
]: 524288
```

在这个例子中，该调用栈分配了 1MB 但只释放了 512KB，可能存在内存泄漏。
