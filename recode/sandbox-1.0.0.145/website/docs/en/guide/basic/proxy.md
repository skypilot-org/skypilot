# Preview Proxy

AIO Sandbox includes built-in proxy endpoints that allow you to preview and test web applications and services directly from your development environment.

![](/images/port-preview.png)

## SubDomain Proxy(Recommend)

Any domain name that satisfies the `${port}-${domain}` format will be forwarded to the corresponding port within the sandbox.

![](/images/subdomain-proxy.png)

## SubPath Proxy Types

### Frontend Application Proxy

Access frontend applications using the absolute proxy:

```
http://localhost:8080/absproxy/{port}/
```

**Use cases:**
- React, Vue, Angular applications
- Static site generators
- Development servers with asset bundling
- Any frontend framework with dev server

**Example:**
```bash
# Start a React app on port 3000
cd /workspace/my-react-app
npm start

# Access via proxy
# Browser: http://localhost:8080/absproxy/3000/
```

### Backend Service Proxy

Access backend services using the relative proxy:

```
http://localhost:8080/proxy/{port}/
```

**Use cases:**
- API servers
- Backend microservices
- Database admin interfaces
- Development tools and utilities

**Example:**
```bash
# Start an Express API on port 4000
cd /workspace/my-api
npm start

# Access via proxy
# API endpoint: http://localhost:8080/proxy/4000/api/users
```

## Next Steps

- **Terminal Integration**: Control services via shell → [Shell API](/api/)
- **File Operations**: Manage application files → [File API](/api/)
- **Browser Automation**: Test applications → [Browser Guide](/guide/basic/browser)
