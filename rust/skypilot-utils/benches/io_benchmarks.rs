use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use skypilot_utils::io_utils::{find_free_port, hash_file, read_last_n_lines};
use std::io::Write;
use tempfile::NamedTempFile;

fn bench_read_last_n_lines(c: &mut Criterion) {
    let mut group = c.benchmark_group("read_last_n_lines");

    for size in [100, 1000, 10000].iter() {
        let mut file = NamedTempFile::new().unwrap();
        for i in 0..*size {
            writeln!(file, "Line number {}", i).unwrap();
        }
        file.flush().unwrap();
        let path = file.path().to_str().unwrap().to_string();

        group.bench_with_input(BenchmarkId::from_parameter(size), size, |b, _| {
            b.iter(|| read_last_n_lines(black_box(&path), black_box(10)))
        });
    }

    group.finish();
}

fn bench_hash_file(c: &mut Criterion) {
    let mut group = c.benchmark_group("hash_file");

    // Create test file with 1MB of data
    let mut file = NamedTempFile::new().unwrap();
    let data = vec![0u8; 1024 * 1024]; // 1MB
    file.write_all(&data).unwrap();
    file.flush().unwrap();
    let path = file.path().to_str().unwrap().to_string();

    for algo in ["md5", "sha256", "sha512"].iter() {
        group.bench_with_input(BenchmarkId::from_parameter(algo), algo, |b, algo| {
            b.iter(|| hash_file(black_box(&path), black_box(algo)))
        });
    }

    group.finish();
}

fn bench_find_free_port(c: &mut Criterion) {
    c.bench_function("find_free_port", |b| {
        b.iter(|| find_free_port(black_box(10000)))
    });
}

criterion_group!(
    benches,
    bench_read_last_n_lines,
    bench_hash_file,
    bench_find_free_port
);
criterion_main!(benches);
