//! # Styx Web UI
//!
//! Modern web interface built with Leptos (Rust WASM).

use leptos::*;
use leptos_router::*;

mod api;
mod components;
mod pages;

pub use components::*;
pub use pages::*;

/// Main app component
#[component]
pub fn App() -> impl IntoView {
    view! {
        <Router>
            <nav class="navbar">
                <div class="container">
                    <h1 class="logo">"?? Styx"</h1>
                    <ul class="nav-links">
                        <li><A href="/">"Dashboard"</A></li>
                        <li><A href="/tasks">"Tasks"</A></li>
                        <li><A href="/instances">"Instances"</A></li>
                        <li><A href="/submit">"Submit"</A></li>
                    </ul>
                </div>
            </nav>

            <main class="container">
                <Routes>
                    <Route path="/" view=HomePage />
                    <Route path="/tasks" view=TasksPage />
                    <Route path="/instances" view=InstancesPage />
                    <Route path="/submit" view=SubmitPage />
                    <Route path="/*any" view=NotFound />
                </Routes>
            </main>

            <footer class="footer">
                <div class="container">
                    <p>"Built with Rust + Leptos | Styx v0.1.0-alpha"</p>
                </div>
            </footer>
        </Router>
    }
}

/// Mount the app to the DOM
pub fn mount_to_body() {
    console_error_panic_hook::set_once();
    mount_to_body(|| view! { <App /> });
}

#[component]
fn NotFound() -> impl IntoView {
    view! {
        <div class="not-found">
            <h1>"404 - Page Not Found"</h1>
            <p><A href="/">"Go back home"</A></p>
        </div>
    }
}
