//! Page components

use leptos::*;

/// Home page (Dashboard)
#[component]
pub fn HomePage() -> impl IntoView {
    view! {
        <div class="page-home">
            <h1>"?? Styx Dashboard"</h1>

            <div class="stats-grid">
                <div class="stat-card">
                    <h3>"Tasks"</h3>
                    <p class="stat-number">"0"</p>
                    <p class="stat-label">"Total"</p>
                </div>

                <div class="stat-card">
                    <h3>"Instances"</h3>
                    <p class="stat-number">"0"</p>
                    <p class="stat-label">"Active"</p>
                </div>

                <div class="stat-card">
                    <h3>"Jobs"</h3>
                    <p class="stat-number">"0"</p>
                    <p class="stat-label">"Running"</p>
                </div>

                <div class="stat-card">
                    <h3>"Status"</h3>
                    <p class="stat-number">"?"</p>
                    <p class="stat-label">"Healthy"</p>
                </div>
            </div>
        </div>
    }
}

/// Tasks page
#[component]
pub fn TasksPage() -> impl IntoView {
    view! {
        <div class="page-tasks">
            <h1>"?? Tasks"</h1>
            <p class="empty-state">"No tasks yet."</p>
        </div>
    }
}

/// Instances page
#[component]
pub fn InstancesPage() -> impl IntoView {
    view! {
        <div class="page-instances">
            <h1>"?? Instances"</h1>
            <p class="empty-state">"No instances provisioned."</p>
        </div>
    }
}

/// Submit task page
#[component]
pub fn SubmitPage() -> impl IntoView {
    let (task_name, set_task_name) = create_signal(String::new());
    let (command, set_command) = create_signal(String::new());

    let on_submit = move |ev: leptos::ev::SubmitEvent| {
        ev.prevent_default();
        logging::log!("Submitting: {} - {}", task_name.get(), command.get());
    };

    view! {
        <div class="page-submit">
            <h1>"? Submit Task"</h1>

            <form on:submit=on_submit>
                <input
                    type="text"
                    placeholder="Task name"
                    on:input=move |ev| set_task_name.set(event_target_value(&ev))
                />
                <input
                    type="text"
                    placeholder="Command"
                    on:input=move |ev| set_command.set(event_target_value(&ev))
                />
                <button type="submit">"Submit"</button>
            </form>
        </div>
    }
}
