//! Reusable UI components

use leptos::*;

/// Task card component
#[component]
pub fn TaskCard(
    id: String,
    name: String,
    status: String,
    created_at: String,
) -> impl IntoView {
    let status_class = match status.as_str() {
        "Running" => "status-running",
        "Completed" => "status-completed",
        "Failed" => "status-failed",
        _ => "status-pending",
    };

    view! {
        <div class="task-card">
            <div class="task-header">
                <h3>{name}</h3>
                <span class={format!("status {}", status_class)}>{status}</span>
            </div>
            <div class="task-body">
                <p class="task-id">"ID: "{id}</p>
                <p class="task-time">"Created: "{created_at}</p>
            </div>
        </div>
    }
}

/// Instance card component
#[component]
pub fn InstanceCard(
    id: String,
    name: String,
    provider: String,
    state: String,
) -> impl IntoView {
    let provider_icon = match provider.as_str() {
        "AWS" => "??",
        "GCP" => "???",
        "Kubernetes" => "?",
        _ => "??",
    };

    view! {
        <div class="instance-card">
            <div class="instance-header">
                <h3>{provider_icon}" "{name}</h3>
                <span class="state">{state}</span>
            </div>
            <div class="instance-body">
                <p>"ID: "{id}</p>
                <p>"Provider: "{provider}</p>
            </div>
        </div>
    }
}

/// Loading spinner
#[component]
pub fn LoadingSpinner() -> impl IntoView {
    view! {
        <div class="loading-spinner">
            <div class="spinner"></div>
        </div>
    }
}
