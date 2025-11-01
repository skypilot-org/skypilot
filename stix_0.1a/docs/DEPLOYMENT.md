# ?? Styx Deployment Guide

Complete guide for deploying Styx in production.

---

## ?? **Quick Start**

### **Local Development**
```bash
# Build
cargo build --release

# Run server
./target/release/styx-server

# Run agent (in another terminal)
export STYX_SERVER_URL=http://localhost:8080
./target/release/styx-agent

# Use CLI
./target/release/styx version
./target/release/styx run --name test echo "Hello!"
```

---

## ?? **Docker**

### **Build Image**
```bash
docker build -t styx:latest .
```

### **Run Server**
```bash
docker run -d \
  --name styx-server \
  -p 8080:8080 \
  -e RUST_LOG=info \
  styx:latest styx-server
```

### **Run Agent**
```bash
docker run -d \
  --name styx-agent \
  -e STYX_SERVER_URL=http://server:8080 \
  styx:latest styx-agent
```

### **Docker Compose**
```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

---

## ?? **Kubernetes**

### **Deploy Server**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: styx-server
spec:
  replicas: 3
  selector:
    matchLabels:
      app: styx-server
  template:
    metadata:
      labels:
        app: styx-server
    spec:
      containers:
      - name: styx-server
        image: styx:latest
        ports:
        - containerPort: 8080
        env:
        - name: RUST_LOG
          value: "info"
        - name: DATABASE_URL
          value: "sqlite:///data/styx.db"
```

### **Service**
```yaml
apiVersion: v1
kind: Service
metadata:
  name: styx-server
spec:
  selector:
    app: styx-server
  ports:
  - port: 80
    targetPort: 8080
  type: LoadBalancer
```

### **Deploy**
```bash
kubectl apply -f k8s/
```

---

## ?? **Production Setup**

### **1. Database**
```bash
# Use PostgreSQL instead of SQLite
export DATABASE_URL="postgresql://user:pass@host:5432/styx"
```

### **2. Environment Variables**
```bash
# Server
export RUST_LOG=info
export DATABASE_URL=postgresql://...
export STYX_SERVER_PORT=8080

# Agent
export STYX_SERVER_URL=http://styx-server:8080
export STYX_AGENT_ID=agent-001
```

### **3. TLS/HTTPS**
Use nginx or traefik as reverse proxy:

```nginx
server {
    listen 443 ssl;
    server_name styx.example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
    }
}
```

---

## ?? **Monitoring**

### **Health Checks**
```bash
# Server health
curl http://localhost:8080/health

# Response:
{
  "status": "healthy",
  "version": "0.1.0-alpha",
  "service": "styx-server"
}
```

### **Prometheus Metrics** (TODO)
```bash
curl http://localhost:8080/metrics
```

---

## ?? **Configuration**

### **Server Config**
```yaml
# config.yaml
server:
  port: 8080
  workers: 4

database:
  url: "postgresql://..."
  max_connections: 10

scheduler:
  max_concurrent: 100
```

### **Agent Config**
```yaml
# agent.yaml
server_url: "http://styx-server:8080"
heartbeat_interval: 30s
max_concurrent_tasks: 10
```

---

## ?? **Security**

### **1. Authentication**
```bash
# Generate JWT secret
export JWT_SECRET=$(openssl rand -base64 32)
```

### **2. Network Security**
- Use TLS for all connections
- Firewall rules for port 8080
- VPC/private networks

### **3. Secrets Management**
Use Kubernetes secrets or Vault:
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: styx-secrets
type: Opaque
data:
  jwt-secret: <base64-encoded>
  database-url: <base64-encoded>
```

---

## ?? **Scaling**

### **Horizontal Scaling**
```bash
# Kubernetes
kubectl scale deployment styx-server --replicas=5

# Docker Compose
docker-compose up --scale server=3 --scale agent=5
```

### **Vertical Scaling**
Adjust resource limits:
```yaml
resources:
  requests:
    memory: "512Mi"
    cpu: "500m"
  limits:
    memory: "2Gi"
    cpu: "2000m"
```

---

## ?? **Updates**

### **Rolling Update**
```bash
# Build new image
docker build -t styx:v2 .

# Update deployment
kubectl set image deployment/styx-server styx-server=styx:v2

# Rollback if needed
kubectl rollout undo deployment/styx-server
```

---

## ?? **Troubleshooting**

### **Check Logs**
```bash
# Docker
docker logs styx-server

# Kubernetes
kubectl logs -f deployment/styx-server

# Direct
RUST_LOG=debug ./styx-server
```

### **Common Issues**

**Port already in use:**
```bash
lsof -i :8080
kill -9 <PID>
```

**Database connection:**
```bash
# Test connection
psql $DATABASE_URL

# Check permissions
```

**Agent can't connect:**
```bash
# Check network
ping styx-server
curl http://styx-server:8080/health
```

---

## ? **Production Checklist**

- [ ] Use PostgreSQL (not SQLite)
- [ ] Enable TLS/HTTPS
- [ ] Configure authentication
- [ ] Set up monitoring
- [ ] Configure backups
- [ ] Set resource limits
- [ ] Use secrets management
- [ ] Configure logging
- [ ] Set up health checks
- [ ] Plan disaster recovery

---

**?? Ready for production!** ??
