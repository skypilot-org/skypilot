#!/usr/sbin/dtrace -s

/*
 * macOS 内存分配追踪脚本 (使用 dtrace)
 * 
 * 使用方法：
 *   sudo dtrace -s malloc_macos.d -p <PID>
 *   或者
 *   sudo ./malloc_macos.d -p <PID>
 * 
 * 功能：
 * - 追踪 malloc/free/calloc/realloc 调用
 * - 按调用栈统计内存分配和释放
 * - 每 10 秒输出统计信息
 */

#pragma D option quiet
#pragma D option defaultargs

dtrace:::BEGIN
{
    printf("开始追踪内存分配... (每10秒输出统计)\n");
    printf("按 Ctrl+C 停止\n\n");
}

/* malloc 追踪 */
pid$target::malloc:entry
{
    self->size = arg0;
}

pid$target::malloc:return
/self->size/
{
    @alloc_bytes[ustack()] = sum(self->size);
    @alloc_count[ustack()] = count();
    self->size = 0;
}

/* free 追踪 */
pid$target::free:entry
/arg0 != 0/
{
    @free_count[ustack()] = count();
}

/* calloc 追踪 */
pid$target::calloc:entry
{
    self->total = arg0 * arg1;
}

pid$target::calloc:return
/self->total/
{
    @alloc_bytes[ustack()] = sum(self->total);
    @alloc_count[ustack()] = count();
    self->total = 0;
}

/* realloc 追踪 */
pid$target::realloc:entry
{
    self->new_size = arg1;
}

pid$target::realloc:return
/self->new_size/
{
    @alloc_bytes[ustack()] = sum(self->new_size);
    @alloc_count[ustack()] = count();
    self->new_size = 0;
}

/* 周期性输出 */
tick-10s
{
    printf("\n================== %Y ==================\n", walltimestamp);
    
    printf("\nTop allocation sites by BYTES:\n");
    trunc(@alloc_bytes, 10);
    printa("Bytes: %@d\n%k\n", @alloc_bytes);
    
    printf("\nTop allocation sites by COUNT:\n");
    trunc(@alloc_count, 10);
    printa("Count: %@d\n%k\n", @alloc_count);
    
    printf("\nTop free call sites by COUNT:\n");
    trunc(@free_count, 10);
    printa("Count: %@d\n%k\n", @free_count);
    
    printf("\n注意：查看 bytes 很高但 free count 很低的调用栈\n");
}

/* 清理 */
dtrace:::END
{
    printf("\n最终统计：\n");
    printf("总分配字节数：\n");
    printa("Bytes: %@d\n%k\n", @alloc_bytes);
}
