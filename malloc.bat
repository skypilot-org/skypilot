#!/usr/bin/env bpftrace

/*
 * 内存分配追踪脚本 - 支持 malloc/free/mmap/munmap
 * 
 * 使用方法：
 *   sudo bpftrace -p <PID> malloc.bat
 * 
 * 注意事项：
 * 1. macOS 需要安装 bpftrace: brew install bpftrace
 * 2. macOS 可能需要禁用 SIP 或使用不同的跟踪方法
 * 3. 如果库路径错误，请根据系统调整 LIBC 定义
 */

// === 配置区：按需修改 ===
// 不同系统的库路径：
// Linux: "/lib/x86_64-linux-gnu/libc.so.6" 或 "/usr/lib/x86_64-linux-gnu/libc.so.6"
// macOS: "libc" (让 bpftrace 自动查找) 或 "/usr/lib/system/libsystem_c.dylib"
#define LIBC "libc"
// 采样降低开销：1 表示全量，10 表示 1/10 采样
#define SAMPLE 1

// === 内部状态 ===
// @ptr_size        // void* -> size_t（malloc 系列）
// @ptr_stack       // void* -> ustack at allocation site  
// @ptr_is_malloc   // void* -> 1  (标识这是 malloc/calloc/realloc 的块)

// 统计（以"分配栈"为 key 聚合）：
// @by_alloc_stack_bytes
// @by_free_stack_bytes  

// mmap/munmap（以"调用栈"为 key 聚合）：
// @mmap_bytes   
// @munmap_bytes 

// 临时存储（每线程）
// @tmp_sz[tid]
// @tmp_nm[tid] 
// @tmp_ptr[tid]

// ---------- malloc ----------
uprobe:LIBC:malloc
/ (SAMPLE == 1) || (nsecs % SAMPLE == 0) /
{
  @tmp_sz[tid] = arg0;
}
uretprobe:LIBC:malloc
/ @tmp_sz[tid] /
{
  $sz = @tmp_sz[tid]; @tmp_sz[tid] = 0;
  @ptr_size[retval] = $sz;
  @ptr_stack[retval] = ustack();
  @ptr_is_malloc[retval] = 1;
  @by_alloc_stack_bytes[@ptr_stack[retval]] = sum($sz);
}

// ---------- calloc ----------
uprobe:LIBC:calloc
/ (SAMPLE == 1) || (nsecs % SAMPLE == 0) /
{
  @tmp_nm[tid] = arg0; @tmp_sz[tid] = arg1;
}
uretprobe:LIBC:calloc
/ @tmp_nm[tid] && @tmp_sz[tid] /
{
  $total = @tmp_nm[tid] * @tmp_sz[tid];
  @tmp_nm[tid] = 0; @tmp_sz[tid] = 0;
  @ptr_size[retval] = $total;
  @ptr_stack[retval] = ustack();
  @ptr_is_malloc[retval] = 1;
  @by_alloc_stack_bytes[@ptr_stack[retval]] = sum($total);
}

// ---------- realloc ----------
uprobe:LIBC:realloc
/ (SAMPLE == 1) || (nsecs % SAMPLE == 0) /
{
  @tmp_ptr[tid] = arg0; @tmp_sz[tid] = arg1;
}
uretprobe:LIBC:realloc
/ 1 /
{
  $oldp = @tmp_ptr[tid]; $newsz = @tmp_sz[tid];
  @tmp_ptr[tid] = 0; @tmp_sz[tid] = 0;

  // 旧块释放（按原分配栈记到 free 侧）
  if ($oldp && @ptr_is_malloc[$oldp]) {
    $oldsz = @ptr_size[$oldp];
    $st = @ptr_stack[$oldp];
    @by_free_stack_bytes[$st] = sum($oldsz);
    delete(@ptr_size[$oldp]);
    delete(@ptr_stack[$oldp]);
    delete(@ptr_is_malloc[$oldp]);
  }

  // 新块分配（记到 alloc 侧）
  if (retval) {
    @ptr_size[retval] = $newsz;
    @ptr_stack[retval] = ustack();
    @ptr_is_malloc[retval] = 1;
    @by_alloc_stack_bytes[@ptr_stack[retval]] = sum($newsz);
  }
}

// ---------- free ----------
uprobe:LIBC:free
/ 1 /
{
  $p = arg0;
  if ($p && @ptr_is_malloc[$p]) {
    $sz = @ptr_size[$p];
    $st = @ptr_stack[$p];
    @by_free_stack_bytes[$st] = sum($sz);
    delete(@ptr_size[$p]);
    delete(@ptr_stack[$p]);
    delete(@ptr_is_malloc[$p]);
  }
}

// ---------- mmap (匿名映射通常走这里) ----------
uprobe:LIBC:mmap
/ (SAMPLE == 1) || (nsecs % SAMPLE == 0) /
{
  // mmap(void* addr, size_t length, int prot, int flags, int fd, off_t off)
  @tmp_sz[tid] = arg1;
}
uretprobe:LIBC:mmap
/ @tmp_sz[tid] /
{
  $len = @tmp_sz[tid]; @tmp_sz[tid] = 0;
  // 直接按 mmap 调用栈累计（不做指针对应，避免与 malloc 双算）
  @mmap_bytes[ustack()] = sum($len);
}

// ---------- munmap ----------
uprobe:LIBC:munmap
/ 1 /
{
  // munmap(void* addr, size_t length)
  @munmap_bytes[ustack()] = sum(arg1);
}

// ---------- 周期性输出 ----------
interval:s:10
{
  printf("\n================== %s ==================\n", strftime("%H:%M:%S", nsecs));
  printf("Top allocation sites (malloc family) - BYTES allocated:\n");
  print(@by_alloc_stack_bytes, 15);

  printf("\nTop free sites (charged back to original alloc stacks) - BYTES freed:\n");
  print(@by_free_stack_bytes, 15);

  printf("\nTop mmap call stacks - BYTES mmapped:\n");
  print(@mmap_bytes, 10);

  printf("\nTop munmap call stacks - BYTES munmapped:\n");
  print(@munmap_bytes, 10);

  printf("\n(Hint) 关注：某个分配栈在 alloc 侧很高，而 free 侧明显偏低；"
         "或者 mmap 明显高于 munmap 的调用栈，即可能是 [anon] 增长的来源。\n");
}

/*
 * 故障排除：
 * 
 * 1. 如果出现 "uprobe target file 'libc' does not exist" 错误：
 *    - Linux: 将 LIBC 改为完整路径，如 "/lib/x86_64-linux-gnu/libc.so.6"
 *    - macOS: 尝试 "/usr/lib/system/libsystem_c.dylib" 或安装 bpftrace
 * 
 * 2. 如果在 macOS 上运行失败：
 *    - 安装 bpftrace: brew install bpftrace
 *    - 可能需要禁用 SIP 或使用 dtrace 替代
 * 
 * 3. 权限问题：
 *    - 必须使用 sudo 运行
 *    - 确保目标进程存在且可访问
 */
