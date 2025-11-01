# STIX MCP Admin UI

Web-based administration interface for the STIX MCP Server.

## Features

- 🔑 **Agent & API Key Management** - Create, view, and manage AI agents and their API tokens
- 📊 **Real-time Dashboard** - Monitor server statistics and agent activities
- 🔒 **File Lock Management** - View and force-unlock files when needed
- 📝 **Activity Logs** - Track all agent actions and tool executions
- 🎯 **Work Target Management** - Define and assign tasks to agents
- ⚙️ **Configuration** - Manage server settings

## Quick Start

### Development

```bash
cd admin-ui

# Install dependencies
npm install

# Start development server (proxies API to localhost:8080)
npm run dev

# Open http://localhost:3000
```

### Production Build

```bash
# Build for production
npm run build

# Preview production build
npm run preview
```

### Deploy with Server

```bash
# Build UI
cd admin-ui
npm run build

# Copy to server static files
cp -r dist ../static/admin-ui

# Server will serve UI at /admin/ui
```

## Default Credentials

**Admin Password:** `admin123`

⚠️ **CHANGE THIS IN PRODUCTION!** Set via environment variable `MCP_ADMIN_PASSWORD`.

## API Proxy

The dev server proxies `/api/*` to `http://localhost:8080`. Make sure the MCP server is running:

```bash
cd ..
cargo run
```

## Screenshots

### Dashboard
![Dashboard](docs/screenshots/dashboard.png)

### Agent Management
![Agents](docs/screenshots/agents.png)

## Technology Stack

- **React 18** - UI framework
- **TypeScript** - Type safety
- **Vite** - Build tool
- **TailwindCSS** - Styling
- **Tanstack Query** - Data fetching
- **Axios** - HTTP client
- **Lucide React** - Icons
- **Recharts** - Charts
- **date-fns** - Date formatting

## Project Structure

```
admin-ui/
├── src/
│   ├── components/     # Reusable components
│   │   └── Layout.tsx  # Main layout with sidebar
│   ├── pages/          # Page components
│   │   ├── Dashboard.tsx
│   │   ├── Agents.tsx
│   │   ├── Locks.tsx
│   │   ├── Logs.tsx
│   │   ├── WorkTargets.tsx
│   │   └── Configuration.tsx
│   ├── lib/
│   │   └── api.ts      # API client
│   ├── App.tsx         # Main app component
│   ├── main.tsx        # Entry point
│   └── index.css       # Global styles
├── public/             # Static assets
├── index.html          # HTML template
├── package.json
├── tsconfig.json
├── vite.config.ts
└── tailwind.config.js
```

## Environment Variables

Create `.env.local`:

```bash
# API base URL (optional, defaults to /api)
VITE_API_BASE_URL=http://localhost:8080
```

## Development

### Adding a New Page

1. Create component in `src/pages/NewPage.tsx`
2. Add route in `src/App.tsx`
3. Add navigation item in `src/components/Layout.tsx`

### API Client Usage

```typescript
import { apiClient } from './lib/api';

// Login
await apiClient.adminLogin('password');

// Create agent
const { agent_id, token } = await apiClient.createAgent('Agent-A', [
  'LockFiles',
  'UpdateProgress',
]);

// List agents
const agents = await apiClient.listAgents();
```

## Troubleshooting

### API Connection Issues

1. Check if MCP server is running: `curl http://localhost:8080/health`
2. Check proxy configuration in `vite.config.ts`
3. Clear browser cache and reload

### Build Errors

```bash
# Clear node_modules and reinstall
rm -rf node_modules package-lock.json
npm install

# Clear Vite cache
rm -rf node_modules/.vite
```

## Contributing

1. Follow the existing code style
2. Use TypeScript for type safety
3. Keep components small and focused
4. Add error handling for all API calls

## License

See [LICENSE](../../LICENSE) in the project root.
