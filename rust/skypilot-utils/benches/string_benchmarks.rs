use criterion::{black_box, criterion_group, criterion_main, Criterion};
use skypilot_utils::string_utils::{base36_encode, format_float, truncate_long_string};

fn bench_base36_encode(c: &mut Criterion) {
    c.bench_function("base36_encode", |b| {
        b.iter(|| base36_encode(black_box("deadbeef")))
    });
}

fn bench_format_float(c: &mut Criterion) {
    let mut group = c.benchmark_group("format_float");

    for value in [0.123, 1234.5, 1234567.89, 1.23e12].iter() {
        group.bench_with_input(format!("{}", value), value, |b, val| {
            b.iter(|| format_float(black_box(*val), black_box(2)))
        });
    }

    group.finish();
}

fn bench_truncate_long_string(c: &mut Criterion) {
    let long_string = "A".repeat(1000);
    c.bench_function("truncate_long_string", |b| {
        b.iter(|| truncate_long_string(black_box(&long_string), black_box(80), black_box("...")))
    });
}

criterion_group!(
    benches,
    bench_base36_encode,
    bench_format_float,
    bench_truncate_long_string
);
criterion_main!(benches);
