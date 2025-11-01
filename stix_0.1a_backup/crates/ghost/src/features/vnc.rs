//! VNC Server - Visual browser interaction
//!
//! Provides VNC access at /vnc/index.html

use std::process::Stdio;
use tokio::process::Command;

/// VNC Server manager
pub struct VncServer {
    display: String,
    port: u16,
}

impl VncServer {
    /// Create new VNC server
    pub fn new(display: impl Into<String>, port: u16) -> Self {
        Self {
            display: display.into(),
            port,
        }
    }

    /// Start VNC server in cave
    pub async fn start(&self, cave_id: &str) -> anyhow::Result<()> {
        println!("???  Starting VNC server on port {}...", self.port);

        // Install VNC components
        let install_cmd = r#"
            apt-get update -qq
            apt-get install -y \
                x11vnc \
                xvfb \
                fluxbox \
                websockify \
                novnc \
                supervisor
        "#;

        self.exec_in_cave(cave_id, install_cmd).await?;

        // Start Xvfb (virtual display)
        let xvfb_cmd = format!(
            "Xvfb {} -screen 0 1920x1080x24 &",
            self.display
        );
        self.exec_in_cave(cave_id, &xvfb_cmd).await?;

        // Start window manager
        let wm_cmd = format!(
            "DISPLAY={} fluxbox &",
            self.display
        );
        self.exec_in_cave(cave_id, &wm_cmd).await?;

        // Start VNC server
        let vnc_cmd = format!(
            "x11vnc -display {} -forever -shared -rfbport {} &",
            self.display, self.port
        );
        self.exec_in_cave(cave_id, &vnc_cmd).await?;

        // Start noVNC (web interface)
        let novnc_port = self.port + 1;
        let novnc_cmd = format!(
            "websockify --web=/usr/share/novnc {} localhost:{} &",
            novnc_port, self.port
        );
        self.exec_in_cave(cave_id, &novnc_cmd).await?;

        println!("   ? VNC server started!");
        println!("      Access at: http://localhost:{}/vnc.html", novnc_port);

        Ok(())
    }

    /// Execute command in cave
    async fn exec_in_cave(&self, cave_id: &str, command: &str) -> anyhow::Result<()> {
        Command::new("docker")
            .args(&["exec", cave_id, "bash", "-c", command])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .await?;
        Ok(())
    }
}
