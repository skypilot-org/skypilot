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

            <div class="quick-actions">
                <h2>"Quick Actions"</h2>
                <div class="button-group">
                    <a href="/submit" class="btn btn-primary">"Submit Task"</a>
                    <a href="/tasks" class="btn btn-secondary">"View Tasks"</a>
                    <a href="/instances" class="btn btn-secondary">"Manage Instances"</a>
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
            
            <div class="task-filters">
                <select>
                    <option>"All"</option>
                    <option>"Running"</option>
                    <option>"Completed"</option>
                    <option>"Failed"</option>
                </select>
            </div>

            <div class="tasks-list">
                <p class="empty-state">"No tasks yet. Submit your first task!"</p>
            </div>
        </div>
    }
}

/// Instances page
#[component]
pub fn InstancesPage() -> impl IntoView {
    view! {
        <div class="page-instances">
            <h1>"?? Instances"</h1>
            
            <div class="instance-filters">
                <select>
                    <option>"All Providers"</option>
                    <option>"AWS"</option>
                    <option>"GCP"</option>
                    <option>"Kubernetes"</option>
                </select>
            </div>

            <div class="instances-list">
                <p class="empty-state">"No instances provisioned."</p>
            </div>
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
        
        let name = task_name.get();
        let cmd = command.get();
        
        logging::log!("Submitting task: {} - {}", name, cmd);
        // TODO: Send to API
    };

    view! {
        <div class="page-submit">
            <h1>"? Submit Task"</h1>
            
            <form on:submit=on_submit class="submit-form">
                <div class="form-group">
                    <label>"Task Name"</label>
                    <input
                        type="text"
                        placeholder="my-task"
                        on:input=move |ev| set_task_name.set(event_target_value(&ev))
                        prop:value=task_name
                    />
                </div>

                <div class="form-group">
                    <label>"Command"</label>
                    <input
                        type="text"
                        placeholder="echo Hello"
                        on:input=move |ev| set_command.set(event_target_value(&ev))
                        prop:value=command
                    />
                </div>

                <div class="form-group">
                    <label>"Priority"</label>
                    <select>
                        <option>"Normal"</option>
                        <option>"High"</option>
                        <option>"Critical"</option>
                        <option>"Low"</option>
                    </select>
                </div>

                <button type="submit" class="btn btn-primary">"Submit Task"</button>
            </form>
        </div>
    }
}
