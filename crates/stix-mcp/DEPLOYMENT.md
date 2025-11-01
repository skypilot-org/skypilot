# STIX MCP Server - Deployment Guide

## 🚀 Production Deployment

### Voraussetzungen

- Docker & Docker Compose installiert
- Zugriff auf Server/Cloud-Instanz
- Ports 8080 (oder gewünschter Port) verfügbar
- Ausreichend Festplattenspeicher für Logs

### Deployment-Optionen

## Option 1: Docker Compose (Empfohlen)

### Setup

```bash
# Repository klonen
git clone <your-repo-url>
cd stix/crates/stix-mcp

# .env konfigurieren
cp .env.example .env
nano .env  # Anpassen

# Workspace-Verzeichnis erstellen
mkdir -p workspace logs

# Docker Compose starten
docker-compose up -d

# Logs prüfen
docker-compose logs -f
```

### Konfiguration

Bearbeite `docker-compose.yml`:

```yaml
services:
  stix-mcp-server:
    environment:
      - MCP_HOST=0.0.0.0
      - MCP_PORT=8080
      - MCP_WORKSPACE_ROOT=/workspace
      - MCP_LOG_FILE=/app/logs/mcp-audit.log
      - MCP_RATE_LIMIT=100  # Anpassen nach Bedarf
      - RUST_LOG=stix_mcp=info  # Production: info statt debug
    volumes:
      - ./workspace:/workspace:rw
      - ./logs:/app/logs:rw
    ports:
      - "8080:8080"  # Host:Container
    restart: unless-stopped
```

## Option 2: Standalone Binary

### Build

```bash
# Release Build
cargo build --release --package stix-mcp

# Binary ist in: target/release/stix-mcp
```

### Systemd Service

Erstelle `/etc/systemd/system/stix-mcp.service`:

```ini
[Unit]
Description=STIX MCP Server
After=network.target

[Service]
Type=simple
User=stix
WorkingDirectory=/opt/stix-mcp
Environment="MCP_HOST=0.0.0.0"
Environment="MCP_PORT=8080"
Environment="MCP_WORKSPACE_ROOT=/opt/stix-workspace"
Environment="MCP_LOG_FILE=/var/log/stix-mcp/audit.log"
Environment="MCP_RATE_LIMIT=100"
Environment="RUST_LOG=stix_mcp=info"
ExecStart=/opt/stix-mcp/stix-mcp
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Installation

```bash
# User erstellen
sudo useradd -r -s /bin/false stix

# Verzeichnisse erstellen
sudo mkdir -p /opt/stix-mcp
sudo mkdir -p /opt/stix-workspace
sudo mkdir -p /var/log/stix-mcp

# Binary kopieren
sudo cp target/release/stix-mcp /opt/stix-mcp/

# Berechtigungen setzen
sudo chown -R stix:stix /opt/stix-mcp
sudo chown -R stix:stix /opt/stix-workspace
sudo chown -R stix:stix /var/log/stix-mcp

# Service aktivieren
sudo systemctl daemon-reload
sudo systemctl enable stix-mcp
sudo systemctl start stix-mcp

# Status prüfen
sudo systemctl status stix-mcp
```

## Option 3: Kubernetes

### Deployment

Erstelle `k8s-deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: stix-mcp-server
  labels:
    app: stix-mcp
spec:
  replicas: 2
  selector:
    matchLabels:
      app: stix-mcp
  template:
    metadata:
      labels:
        app: stix-mcp
    spec:
      containers:
      - name: stix-mcp
        image: your-registry/stix-mcp:latest
        ports:
        - containerPort: 8080
        env:
        - name: MCP_HOST
          value: "0.0.0.0"
        - name: MCP_PORT
          value: "8080"
        - name: MCP_WORKSPACE_ROOT
          value: "/workspace"
        - name: MCP_LOG_FILE
          value: "/app/logs/mcp-audit.log"
        - name: MCP_RATE_LIMIT
          value: "100"
        - name: RUST_LOG
          value: "stix_mcp=info"
        volumeMounts:
        - name: workspace
          mountPath: /workspace
        - name: logs
          mountPath: /app/logs
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
      volumes:
      - name: workspace
        persistentVolumeClaim:
          claimName: stix-workspace-pvc
      - name: logs
        persistentVolumeClaim:
          claimName: stix-logs-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: stix-mcp-service
spec:
  selector:
    app: stix-mcp
  ports:
  - protocol: TCP
    port: 8080
    targetPort: 8080
  type: LoadBalancer
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: stix-workspace-pvc
spec:
  accessModes:
  - ReadWriteMany
  resources:
    requests:
      storage: 10Gi
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: stix-logs-pvc
spec:
  accessModes:
  - ReadWriteMany
  resources:
    requests:
      storage: 5Gi
```

Deploy:

```bash
kubectl apply -f k8s-deployment.yaml
kubectl get pods -l app=stix-mcp
kubectl logs -f deployment/stix-mcp-server
```

## 🔒 Sicherheit

### HTTPS/TLS Setup

#### Mit Nginx Reverse Proxy

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/mcp.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

#### Mit Caddy

```caddyfile
mcp.yourdomain.com {
    reverse_proxy localhost:8080
}
```

### Firewall

```bash
# UFW (Ubuntu)
sudo ufw allow 8080/tcp
sudo ufw enable

# iptables
sudo iptables -A INPUT -p tcp --dport 8080 -j ACCEPT
```

### Token-Verwaltung

**Best Practices:**
- Tokens sicher speichern (Secrets Manager, Vault)
- Regelmäßig rotieren
- Getrennte Tokens pro Agent
- Minimale Permissions pro Agent

## 📊 Monitoring & Logging

### Log-Rotation

Erstelle `/etc/logrotate.d/stix-mcp`:

```
/var/log/stix-mcp/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 stix stix
    sharedscripts
    postrotate
        systemctl reload stix-mcp > /dev/null 2>&1 || true
    endscript
}
```

### Prometheus Monitoring

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'stix-mcp'
    static_configs:
      - targets: ['localhost:8080']
    metrics_path: /metrics  # Wenn implementiert
```

### Grafana Dashboard

Erstelle Dashboard für:
- Request Rate
- Error Rate
- Active Locks
- Agent Activity
- Rate Limit Status

## 🔧 Wartung

### Backup

```bash
#!/bin/bash
# backup.sh

BACKUP_DIR="/backup/stix-mcp"
DATE=$(date +%Y%m%d_%H%M%S)

# Logs sichern
tar -czf "$BACKUP_DIR/logs_$DATE.tar.gz" /var/log/stix-mcp/

# Workspace sichern
tar -czf "$BACKUP_DIR/workspace_$DATE.tar.gz" /opt/stix-workspace/

# Alte Backups löschen (älter als 30 Tage)
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +30 -delete
```

Cron-Job:
```cron
0 2 * * * /opt/stix-mcp/backup.sh
```

### Updates

```bash
# Docker
docker-compose pull
docker-compose up -d

# Systemd
systemctl stop stix-mcp
cp new-binary /opt/stix-mcp/stix-mcp
systemctl start stix-mcp

# Kubernetes
kubectl set image deployment/stix-mcp-server stix-mcp=your-registry/stix-mcp:new-version
```

## 🧪 Testing

### Health Check

```bash
curl http://localhost:8080/health
```

Expected:
```json
{
  "status": "healthy",
  "version": "0.1.0"
}
```

### Load Testing

```bash
# Apache Bench
ab -n 1000 -c 10 http://localhost:8080/health

# wrk
wrk -t4 -c100 -d30s http://localhost:8080/health
```

## 📈 Performance Tuning

### Rust Build Optimizations

In `Cargo.toml`:

```toml
[profile.release]
opt-level = 3
lto = true
codegen-units = 1
strip = true
```

### System Limits

```bash
# Erhöhe File Descriptors
ulimit -n 65536

# In /etc/security/limits.conf:
stix soft nofile 65536
stix hard nofile 65536
```

## 🚨 Troubleshooting

### Server startet nicht

```bash
# Logs prüfen
docker-compose logs -f
journalctl -u stix-mcp -f

# Ports prüfen
netstat -tlnp | grep 8080
lsof -i :8080

# Permissions prüfen
ls -la /opt/stix-workspace
ls -la /var/log/stix-mcp
```

### High Memory Usage

```bash
# Prüfe Ressourcen
docker stats stix-mcp-server

# Reduziere Rate Limit
MCP_RATE_LIMIT=50

# Cleanup alte Locks
curl -X POST http://localhost:8080/tools/execute \
  -H "Authorization: Bearer admin-token" \
  -d '{"tool_name": "cleanup_locks", "parameters": {}}'
```

### Connection Issues

```bash
# Teste Verbindung
telnet localhost 8080

# DNS prüfen
nslookup mcp.yourdomain.com

# Firewall prüfen
sudo iptables -L -n -v
```

## 📞 Support

- GitHub Issues: <repository-url>/issues
- Documentation: <repository-url>/docs
- Email: support@yourdomain.com

## 📝 Changelog

Siehe [CHANGELOG.md](../../CHANGELOG.md) für Versionshistorie.
