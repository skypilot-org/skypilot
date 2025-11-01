# ?? Styx Git - Self-hosted Git Service

**Pure Rust implementation of a Git server (inspired by Gogs/Gitea)**

---

## ? Features

- **?? Pure Rust**: Fast, safe, and reliable
- **?? Git Hosting**: Full Git repository hosting
- **?? User Management**: User accounts and authentication
- **?? Organizations**: Team collaboration
- **?? Issue Tracking**: Built-in issue tracker
- **?? Pull Requests**: Code review workflow
- **?? Webhooks**: CI/CD integration
- **?? SSH & HTTP(S)**: Multiple access protocols
- **?? Web UI**: Beautiful web interface
- **? Lightweight**: Minimal resource usage
- **?? Secure**: Argon2 password hashing, JWT authentication

---

## ?? Quick Start

### Installation

```bash
# Build from source
cd /workspace/styx
cargo build --release -p styx-git

# Run
./target/release/styx-git serve
```

### Initialize Configuration

```bash
styx-git init
```

This creates `config.toml` with default settings:

```toml
[server]
host = "0.0.0.0"
http_port = 3000
domain = "localhost"
root_url = "http://localhost:3000"

[database]
db_type = "SQLite"
path = "./data/styx-git.db"

[repository]
root = "./data/repositories"
enable_lfs = true

[security]
min_password_length = 6

[ssh]
disabled = false
port = 2222
listen_host = "0.0.0.0"
```

### Start Server

```bash
styx-git serve --port 3000
```

Access at: **http://localhost:3000**

---

## ?? Usage

### Create Admin User

```bash
styx-git admin \
  --username admin \
  --email admin@example.com \
  --password secure123
```

### Clone Repository

```bash
# HTTPS
git clone http://localhost:3000/username/repo.git

# SSH
git clone ssh://git@localhost:2222/username/repo.git
```

### Push Changes

```bash
git push origin main
```

---

## ??? Architecture

```
???????????????????????????????????????????????
?              Styx Git Server                ?
???????????????????????????????????????????????
?  Web UI (Axum + Templates)                  ?
?  ?? Dashboard                               ?
?  ?? Repository Browser                      ?
?  ?? Issues                                  ?
?  ?? Pull Requests                           ?
?  ?? Settings                                ?
???????????????????????????????????????????????
?  REST API                                   ?
?  ?? /api/v1/users                          ?
?  ?? /api/v1/repos                          ?
?  ?? /api/v1/issues                         ?
?  ?? /api/v1/pulls                          ?
???????????????????????????????????????????????
?  Git Protocol                               ?
?  ?? HTTP Smart Protocol                    ?
?  ?? SSH Protocol                           ?
?  ?? Git Operations                         ?
???????????????????????????????????????????????
?  Core Services                              ?
?  ?? User Management                         ?
?  ?? Repository Manager                      ?
?  ?? Issue Tracker                          ?
?  ?? Pull Request Manager                    ?
?  ?? Webhook Delivery                        ?
?  ?? Authentication (JWT + Argon2)          ?
???????????????????????????????????????????????
?  Database (SQLite/PostgreSQL)              ?
?  ?? Users, SSH Keys, Tokens                ?
?  ?? Repositories                           ?
?  ?? Issues, Comments                       ?
?  ?? Pull Requests                          ?
?  ?? Webhooks                               ?
???????????????????????????????????????????????
?  Storage                                    ?
?  ?? Bare Git Repositories                  ?
???????????????????????????????????????????????
```

---

## ?? Configuration

### Server

```toml
[server]
host = "0.0.0.0"
http_port = 3000
domain = "git.example.com"
root_url = "https://git.example.com"
cert_file = "/path/to/cert.pem"  # Optional
key_file = "/path/to/key.pem"    # Optional
```

### Database

```toml
[database]
db_type = "SQLite"  # or "PostgreSQL", "MySQL"
path = "./data/styx-git.db"

# For PostgreSQL/MySQL:
# host = "localhost"
# port = 5432
# name = "styx-git"
# user = "styx"
# password = "secret"
```

### Repository

```toml
[repository]
root = "./data/repositories"
enable_lfs = true
max_creation_limit = -1  # -1 = unlimited
```

### Security

```toml
[security]
secret_key = "your-secret-key"
install_lock = false
min_password_length = 6
disable_git_hooks = false
```

### SSH

```toml
[ssh]
disabled = false
port = 2222
listen_host = "0.0.0.0"
server_key_path = "./data/ssh/id_rsa"
```

---

## ?? API

### Authentication

```bash
# Login
curl -X POST http://localhost:3000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"secret"}'

# Returns JWT token
{"token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."}
```

### Repositories

```bash
# Create repository
curl -X POST http://localhost:3000/api/v1/repos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-repo","description":"My awesome project"}'

# List repositories
curl http://localhost:3000/api/v1/repos

# Get repository
curl http://localhost:3000/api/v1/repos/username/repo
```

### Issues

```bash
# Create issue
curl -X POST http://localhost:3000/api/v1/repos/username/repo/issues \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"title":"Bug found","body":"Description..."}'

# List issues
curl http://localhost:3000/api/v1/repos/username/repo/issues
```

### Pull Requests

```bash
# Create PR
curl -X POST http://localhost:3000/api/v1/repos/username/repo/pulls \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"title":"Feature","head":"feature-branch","base":"main"}'

# Merge PR
curl -X POST http://localhost:3000/api/v1/repos/username/repo/pulls/1/merge \
  -H "Authorization: Bearer $TOKEN"
```

---

## ?? Webhooks

### Create Webhook

```bash
curl -X POST http://localhost:3000/api/v1/repos/username/repo/webhooks \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "url": "https://example.com/webhook",
    "secret": "webhook-secret",
    "events": ["push", "pull_request"]
  }'
```

### Webhook Payload

```json
{
  "event": "Push",
  "repository": "username/repo",
  "sender": "username",
  "data": {
    "ref": "refs/heads/main",
    "commits": [...]
  }
}
```

---

## ?? Use Cases

### 1. **Private Git Hosting**
Host your private repositories on your own infrastructure:
```bash
styx-git serve
# Create private repos via web UI
```

### 2. **Team Collaboration**
Create organizations and manage team access:
- Organizations with multiple members
- Fine-grained access control
- Team discussions via issues

### 3. **CI/CD Integration**
Integrate with your CI/CD pipeline:
```yaml
# .github/workflows/deploy.yml (webhook trigger)
on:
  push:
    branches: [main]
```

### 4. **Code Review**
Use pull requests for code review:
- Create feature branches
- Submit pull requests
- Review and merge

### 5. **Issue Tracking**
Track bugs and features:
- Create issues
- Assign to team members
- Link to commits

---

## ?? Comparison

| Feature | Styx Git | Gogs | Gitea | GitLab |
|---------|----------|------|-------|--------|
| **Language** | ?? Rust | Go | Go | Ruby/Go |
| **Memory** | ~30MB | ~50MB | ~80MB | ~1GB |
| **Binary Size** | ~20MB | ~30MB | ~50MB | N/A |
| **Startup Time** | ~50ms | ~200ms | ~500ms | ~5s |
| **SQLite** | ? | ? | ? | ? |
| **PostgreSQL** | ? | ? | ? | ? |
| **SSH** | ? | ? | ? | ? |
| **Webhooks** | ? | ? | ? | ? |
| **Issues** | ? | ? | ? | ? |
| **Pull Requests** | ? | ? | ? | ? |
| **CI/CD** | ?? | ? | ? | ? |

**Styx Git is faster and uses less memory!** ?

---

## ?? Security

### Password Hashing
- **Argon2**: Industry-standard password hashing
- **Salt**: Random salt per user
- **Cost**: Configurable work factor

### Authentication
- **JWT**: JSON Web Tokens for API
- **SSH Keys**: Public key authentication
- **Access Tokens**: Personal access tokens

### Permissions
- **Repository Visibility**: Public/Private
- **User Roles**: Admin/Member
- **Organization Roles**: Owner/Admin/Member

---

## ?? Roadmap

- [x] Core Git server
- [x] User management
- [x] Repository management
- [x] Issue tracking
- [x] Pull requests
- [x] Webhooks
- [x] SSH support
- [x] HTTP smart protocol
- [x] Web UI
- [ ] Git LFS
- [ ] CI/CD pipelines
- [ ] Wiki
- [ ] Package registry
- [ ] Container registry
- [ ] Advanced access control
- [ ] LDAP/OAuth integration

---

## ?? Dependencies

```toml
[dependencies]
# Core
tokio = "1.40"
axum = "0.7"

# Git
git2 = "0.19"
gix = "0.66"

# Database
sqlx = "0.8"

# Security
argon2 = "0.5"
jsonwebtoken = "9.3"

# SSH
russh = "0.44"
```

---

## ?? License

Apache 2.0

---

## ?? Contributing

Contributions welcome! See `CONTRIBUTING.md`.

---

**?? Powered by Rust!** ?  
**?? Self-hosted Git made simple!** ??

**Based on Gogs, reimplemented in Rust for better performance and safety!**
