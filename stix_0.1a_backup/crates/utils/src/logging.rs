//! Logging and tracing setup

use tracing_subscriber::{fmt, layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

/// Initialize logging with tracing
pub fn init() -> anyhow::Result<()> {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));

    tracing_subscriber::registry()
        .with(filter)
        .with(
            fmt::layer()
                .with_target(true)
                .with_thread_ids(true)
                .with_file(true)
                .with_line_number(true),
        )
        .init();

    tracing::info!("Logging initialized");
    Ok(())
}

/// Initialize logging with OpenTelemetry (for production)
#[cfg(feature = "otel")]
pub fn init_with_otel(service_name: &str) -> anyhow::Result<()> {
    use opentelemetry::sdk::Resource;
    use opentelemetry::KeyValue;
    use opentelemetry_otlp::WithExportConfig;
    use tracing_opentelemetry::OpenTelemetryLayer;

    let tracer = opentelemetry_otlp::new_pipeline()
        .tracing()
        .with_exporter(
            opentelemetry_otlp::new_exporter()
                .tonic()
                .with_endpoint("http://localhost:4317"),
        )
        .with_trace_config(
            opentelemetry::sdk::trace::config().with_resource(Resource::new(vec![
                KeyValue::new("service.name", service_name.to_string()),
            ])),
        )
        .install_batch(opentelemetry::runtime::Tokio)?;

    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));

    tracing_subscriber::registry()
        .with(filter)
        .with(OpenTelemetryLayer::new(tracer))
        .with(fmt::layer())
        .init();

    tracing::info!("OpenTelemetry logging initialized for {}", service_name);
    Ok(())
}
