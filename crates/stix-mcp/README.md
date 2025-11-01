# STIX MCP Server

Model Context Protocol (MCP) Server für Team-Kollaboration zwischen KI-Agents in STIX.

## 🎯 Features

- **Authentication & Authorization**: Token-basierte Authentifizierung mit granularen Berechtigungen
- **File Locking**: Conflict-Prevention durch exklusive File-Locks mit Zeitstempeln
- **Progress Tracking**: Automatisches Schreiben und Protokollieren in PROGRESS.md
- **Task Management**: Erstellen und Verwalten von Tasks in TODO.md
- **Codebase Search**: Durchsuchen des Repositories mit verschiedenen Optionen
- **Audit Logging**: Vollständige Protokollierung aller Tool-Ausführungen
- **Rate Limiting**: Schutz vor Überlastung durch Request-Limits pro Agent

## 🏗️ Architektur

```
crates/stix-mcp/
├── src/
│   ├── agent.rs         # Agent-Verwaltung & Permissions
│   ├── locks.rs         # File-Locking System
│   ├── logger.rs        # Audit-Logging & Rate-Limiting
│   ├── tools.rs         # Tool-Implementierungen
│   ├── server.rs        # HTTP Server & API
│   ├── client.rs        # Client-Bibliothek
│   ├── lib.rs           # Library-Exports
│   └── main.rs          # Server-Binary
├── Cargo.toml
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## 🚀 Quick Start

### Lokale Entwicklung

```bash
# Server starten
cd crates/stix-mcp
cargo run

# Mit benutzerdefinierten Einstellungen
MCP_PORT=9000 MCP_WORKSPACE_ROOT=/path/to/workspace cargo run
```

### Docker Deployment

```bash
# Docker Image bauen
docker build -t stix-mcp-server -f crates/stix-mcp/Dockerfile .

# Server starten
docker-compose -f crates/stix-mcp/docker-compose.yml up -d

# Logs anzeigen
docker-compose -f crates/stix-mcp/docker-compose.yml logs -f
```

## 📡 API Endpoints

### Health Check
```bash
curl http://localhost:8080/health
```

### Agent Registrierung
```bash
curl -X POST http://localhost:8080/agents/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Agent-A",
    "permissions": ["LockFiles", "UpdateProgress", "SearchCodebase"]
  }'
```

Response:
```json
{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "token": "your-secret-token"
}
```

### Tool Ausführung
```bash
curl -X POST http://localhost:8080/tools/execute \
  -H "Authorization: Bearer your-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "lock_file",
    "parameters": {
      "file_path": "src/main.rs",
      "duration_secs": 300
    }
  }'
```

## 🔧 Verfügbare Tools

### 1. lock_file
Sperrt eine Datei für exklusiven Zugriff.

**Permission**: `LockFiles`

**Parameter**:
```json
{
  "file_path": "path/to/file.rs",
  "duration_secs": 300  // Optional: Auto-unlock nach N Sekunden
}
```

### 2. unlock_file
Entsperrt eine Datei.

**Permission**: `LockFiles`

**Parameter**:
```json
{
  "file_path": "path/to/file.rs"
}
```

### 3. update_progress
Aktualisiert PROGRESS.md mit neuen Einträgen.

**Permission**: `UpdateProgress`

**Parameter**:
```json
{
  "section": "Feature Implementation",
  "content": "Completed authentication module",
  "append": true
}
```

### 4. issue_task
Erstellt einen neuen Task in TODO.md.

**Permission**: `CreateTasks`

**Parameter**:
```json
{
  "title": "Implement API endpoint",
  "description": "Add REST endpoint for user management",
  "priority": "high",  // Optional
  "assigned_to": "Agent-B"  // Optional
}
```

### 5. search_codebase
Durchsucht das Repository nach Code.

**Permission**: `SearchCodebase`

**Parameter**:
```json
{
  "query": "fn main",
  "file_pattern": "*.rs",  // Optional
  "case_sensitive": false
}
```

### 6. list_locks
Listet alle aktiven File-Locks auf.

**Permission**: `ViewLogs`

**Parameter**: `{}`

## 🧑‍💻 Client-Nutzung

### Rust Client

```rust
use stix_mcp::McpClient;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Client erstellen
    let mut client = McpClient::new("http://localhost:8080".to_string());

    // Als Agent registrieren
    let agent_id = client.register(
        "MyAgent".to_string(),
        vec![
            "LockFiles".to_string(),
            "UpdateProgress".to_string(),
            "SearchCodebase".to_string(),
        ],
    ).await?;

    println!("Registered as agent: {}", agent_id);

    // Datei sperren
    let response = client.lock_file(
        "src/lib.rs".to_string(),
        Some(300),  // 5 Minuten
    ).await?;

    if response.success {
        println!("File locked successfully!");
        
        // Arbeit an der Datei durchführen...
        
        // Datei entsperren
        client.unlock_file("src/lib.rs".to_string()).await?;
        
        // Progress aktualisieren
        client.update_progress(
            "Implementation".to_string(),
            "Updated lib.rs with new features".to_string(),
            true,
        ).await?;
    }

    Ok(())
}
```

### Python Client (Beispiel)

```python
import requests
import json

class McpClient:
    def __init__(self, base_url):
        self.base_url = base_url
        self.token = None
        
    def register(self, name, permissions):
        response = requests.post(
            f"{self.base_url}/agents/register",
            json={"name": name, "permissions": permissions}
        )
        data = response.json()
        self.token = data["token"]
        return data["agent_id"]
    
    def lock_file(self, file_path, duration_secs=None):
        response = requests.post(
            f"{self.base_url}/tools/execute",
            headers={"Authorization": f"Bearer {self.token}"},
            json={
                "tool_name": "lock_file",
                "parameters": {
                    "file_path": file_path,
                    "duration_secs": duration_secs
                }
            }
        )
        return response.json()

# Nutzung
client = McpClient("http://localhost:8080")
agent_id = client.register("PythonAgent", ["LockFiles", "UpdateProgress"])
result = client.lock_file("src/main.py", 300)
```

## 🔐 Permissions

- **ReadFiles**: Dateien lesen (aktuell nicht verwendet, für zukünftige Features)
- **WriteFiles**: Dateien schreiben (aktuell nicht verwendet)
- **LockFiles**: Dateien sperren und entsperren
- **UpdateProgress**: PROGRESS.md aktualisieren
- **CreateTasks**: Tasks in TODO.md erstellen
- **SearchCodebase**: Repository durchsuchen
- **ViewLogs**: Alle Logs anzeigen (nicht nur eigene)

## 📊 Monitoring

### Logs anzeigen

```bash
# Eigene Logs
curl -H "Authorization: Bearer your-token" \
  http://localhost:8080/logs/my

# Alle Logs (erfordert ViewLogs Permission)
curl -H "Authorization: Bearer your-token" \
  http://localhost:8080/logs/all
```

### Audit Log File

Alle Aktionen werden in `MCP_LOG_FILE` protokolliert:

```
[2025-11-01 10:15:23] Agent-A (550e8400...) - lock_file - Lock file for editing - {"file":"src/main.rs"}
[2025-11-01 10:16:45] Agent-A (550e8400...) - unlock_file - Unlock after editing - {"file":"src/main.rs"}
[2025-11-01 10:17:12] Agent-B (661f9511...) - search_codebase - Search for function - {"query":"fn main"}
```

## 🎬 Workflow-Beispiel

### Szenario: Zwei Agents arbeiten zusammen

```rust
// Agent A
let mut agent_a = McpClient::new("http://localhost:8080".to_string());
agent_a.register("Agent-A".to_string(), vec![
    "LockFiles".to_string(),
    "UpdateProgress".to_string(),
]).await?;

// Datei sperren
agent_a.lock_file("src/core/state.rs".to_string(), Some(600)).await?;
println!("Agent A: Working on state.rs...");

// Agent B versucht gleichzeitig zuzugreifen
let mut agent_b = McpClient::new("http://localhost:8080".to_string());
agent_b.register("Agent-B".to_string(), vec![
    "LockFiles".to_string(),
]).await?;

let result = agent_b.lock_file("src/core/state.rs".to_string(), None).await?;
// Fehler: "File is already locked by Agent-A"

// Agent A beendet Arbeit
agent_a.unlock_file("src/core/state.rs".to_string()).await?;
agent_a.update_progress(
    "State Implementation".to_string(),
    "Completed database integration in state.rs".to_string(),
    true,
).await?;

// Jetzt kann Agent B zugreifen
let result = agent_b.lock_file("src/core/state.rs".to_string(), None).await?;
// Erfolg!
```

## 🔧 Konfiguration

### Umgebungsvariablen

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `MCP_HOST` | `0.0.0.0` | Server-Host |
| `MCP_PORT` | `8080` | Server-Port |
| `MCP_WORKSPACE_ROOT` | `.` | Workspace-Verzeichnis |
| `MCP_LOG_FILE` | `mcp-audit.log` | Audit-Log Datei |
| `MCP_RATE_LIMIT` | `100` | Requests pro Minute pro Agent |
| `RUST_LOG` | `stix_mcp=debug` | Logging-Level |

### .env File

Kopiere `.env.example` zu `.env` und passe die Werte an:

```bash
cp .env.example .env
```

## 🧪 Testing

```bash
# Unit Tests
cargo test

# Integration Tests
cargo test --test '*'

# Mit Logs
RUST_LOG=debug cargo test -- --nocapture
```

## 🐛 Troubleshooting

### Server startet nicht

```bash
# Port bereits belegt?
lsof -i :8080

# Workspace-Verzeichnis existiert?
ls -la $MCP_WORKSPACE_ROOT
```

### Rate Limit Errors

Erhöhe `MCP_RATE_LIMIT` oder warte 60 Sekunden zwischen Requests.

### File Lock bleibt hängen

Locks mit Ablaufzeit verwenden oder Admin-Funktion `force_unlock` nutzen.

## 📚 Weiterführende Dokumentation

- [Model Context Protocol Specification](https://modelcontextprotocol.io/)
- [STIX Architecture Documentation](../../docs/)
- [API Examples](./examples/)

## 🤝 Contributing

Contributions sind willkommen! Bitte erstelle Issues oder Pull Requests.

## 📄 License

Siehe [LICENSE](../../LICENSE) im Root des Projekts.
