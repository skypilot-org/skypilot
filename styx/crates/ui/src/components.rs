//! Reusable UI components

use leptos::*;

/// Task card component
#[component]
pub fn TaskCard(
    id: String,
    name: String,
    status: String,
) -> impl IntoView {
    view! {
        <div class="task-card">
            <h3>{name}</h3>
            <p>"ID: "{id}</p>
            <span class="status">{status}</span>
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
