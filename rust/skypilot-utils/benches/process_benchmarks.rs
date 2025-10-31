use criterion::{black_box, criterion_group, criterion_main, Criterion};
use skypilot_utils::process_utils::{
    estimate_fd_for_directory, get_max_workers_for_file_mounts, get_parallel_threads,
    is_process_alive,
};

fn bench_get_parallel_threads(c: &mut Criterion) {
    c.bench_function("get_parallel_threads", |b| {
        b.iter(|| get_parallel_threads(black_box(None)))
    });

    c.bench_function("get_parallel_threads_kubernetes", |b| {
        b.iter(|| get_parallel_threads(black_box(Some("kubernetes"))))
    });
}

fn bench_is_process_alive(c: &mut Criterion) {
    let current_pid = std::process::id() as i32;

    c.bench_function("is_process_alive_current", |b| {
        b.iter(|| is_process_alive(black_box(current_pid)))
    });

    c.bench_function("is_process_alive_nonexistent", |b| {
        b.iter(|| is_process_alive(black_box(9999999)))
    });
}

fn bench_get_max_workers(c: &mut Criterion) {
    c.bench_function("get_max_workers_for_file_mounts", |b| {
        b.iter(|| get_max_workers_for_file_mounts(black_box(10), black_box(100), black_box(None)))
    });
}

fn bench_estimate_fd_for_directory(c: &mut Criterion) {
    c.bench_function("estimate_fd_for_directory", |b| {
        b.iter(|| estimate_fd_for_directory(black_box("/tmp")))
    });
}

criterion_group!(
    benches,
    bench_get_parallel_threads,
    bench_is_process_alive,
    bench_get_max_workers,
    bench_estimate_fd_for_directory
);
criterion_main!(benches);
