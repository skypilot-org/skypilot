# Code Server

AIO Sandbox includes a fully-featured VSCode Server (Code Server) that provides a complete cloud development environment accessible through your web browser.

![](/images/code-server.png)

## Access Code Server

The Code Server is available at:
```
http://localhost:8080/code-server/
```

No authentication required by default - immediate access to a full VSCode environment.

## Features

### Full VSCode Experience
- Complete VSCode interface in the browser
- Extensions marketplace access
- Integrated terminal
- Git integration
- IntelliSense and syntax highlighting
- Debugging capabilities

### File System Integration
Access the same file system as other sandbox components:
- Files created in browser/shell appear instantly
- Edit files from API operations
- Work with downloaded files immediately
- Changes reflect across all interfaces

### Development Tools
Pre-configured development environment:
- Node.js runtime
- Python interpreter
- Git version control
- Package managers (npm, pip, etc.)
- Development servers

## Workspace Setup

### Project Structure
Recommended workspace organization:
```
/home/gem/
├── projects/           # Your development projects
├── Downloads/          # Browser downloads
├── .config/           # Tool configurations
└── workspace/         # Current working directory
```

### Opening Projects
1. Navigate to Code Server interface
2. Open folder: `/home/gem/projects/myproject`
3. Use File → Open Folder or Ctrl+K, Ctrl+O
4. Select project directory

### Creating New Projects
```bash
# Create project structure
mkdir -p /home/gem/projects/new-app
cd /home/gem/projects/new-app

# Initialize project
npm init -y
git init

# Open in Code Server
# Navigate to http://localhost:8080/code-server/
# Open folder: /home/gem/projects/new-app
```
