# ?? Styx Web UI

Modern web interface for Styx, built with **Leptos** (Rust WASM).

## ? Features

- ?? **Dashboard** - Task, instance, job overview
- ?? **Task Management** - Submit, monitor, cancel tasks
- ?? **Instance Management** - View cloud resources
- ? **Real-time Updates** - Live status
- ?? **Modern UI** - Responsive, beautiful

## ?? Quick Start

### **Prerequisites**
```bash
rustup target add wasm32-unknown-unknown
cargo install trunk
```

### **Development**
```bash
cd /workspace/styx/crates/ui
trunk serve
```

Open: http://localhost:8080

### **Build for Production**
```bash
trunk build --release
```

Output: `dist/`

## ??? Architecture

```
ui/
??? src/
?   ??? lib.rs          # Main app
?   ??? pages.rs        # Page components
?   ??? components.rs   # Reusable components
?   ??? api.rs          # Backend API client
??? static/
?   ??? index.html      # HTML template
?   ??? style.css       # Styles
??? Cargo.toml
```

## ?? Stack

- **Leptos** - Reactive UI framework
- **Leptos Router** - Routing
- **WASM** - WebAssembly
- **Gloo** - WASM utilities

## ?? Components

### **Pages**
- `HomePage` - Dashboard
- `TasksPage` - Task list
- `InstancesPage` - Instance list
- `SubmitPage` - Task submission form

### **Components**
- `TaskCard` - Task display
- `InstanceCard` - Instance display
- `LoadingSpinner` - Loading state

## ?? API Integration

```rust
use styx_ui::api;

// Submit task
let response = api::submit_task(SubmitTaskRequest {
    name: "my-task".to_string(),
    command: "echo Hello".to_string(),
    args: None,
}).await?;

// Get tasks
let tasks = api::get_tasks().await?;
```

## ?? Styling

Modern CSS with:
- CSS Grid & Flexbox
- Responsive design
- Smooth transitions
- Custom color scheme

## ?? Deployment

### **With Styx Server**
```bash
# Build UI
trunk build --release

# Serve via styx-server
cargo run --release -p styx-server
```

### **Standalone (Nginx)**
```nginx
server {
    listen 80;
    root /path/to/styx/crates/ui/dist;
    
    location /api {
        proxy_pass http://localhost:8080;
    }
}
```

## ?? Screenshots

(TODO: Add screenshots)

## ?? Learn More

- [Leptos Book](https://leptos-rs.github.io/leptos/)
- [WASM Book](https://rustwasm.github.io/docs/book/)

---

**Built with ?? and Rust ??**
