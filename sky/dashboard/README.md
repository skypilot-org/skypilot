This is a [Next.js](https://nextjs.org/) project bootstrapped with [`create-next-app`](https://github.com/vercel/next.js/tree/canary/packages/create-next-app).

## Getting Started

### Access the dashboard

If you install SkyPilot from the official package, after starting the API server, the dashboard can be accessed at `${API_Server_Endpoint}/dashboard`, for example, it's `http://127.0.0.1:46580/dashboard` for the local API server by default.

If you install SkyPilot from source, before starting the API server, run the following commands to generate the dashboard production build:

1. Install node js and npm:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
nvm install 18
nvm use 18
```

```bash
# You may need to clear your cache if you have permission issues
# npm cache clean --force

# Install all dependencies in the current directory
npm install
# Build
npm run build
```

Then you can start your API server and access the dashboard at `${API_Server_Endpoint}/dashboard`.

### Run dashboard development server

If you want to update the dashboard files or debug the dashboard, you can run the development server following the instructions.

Install dependencies:

```bash
# You may need to clear your cache if you have permission issues
# npm cache clean --force

# Install all dependencies in the current directory
npm install
```

By default, the dashboard will connect to your local API server at `http://127.0.0.1:46580`. If you want to connect to another API server, set the environment variable:

```bash
export SKYPILOT_API_SERVER_ENDPOINT=http://username:password@skypilot-api.domain.com:30050
```

Run the development server:

```bash
npm run dev
```

Open [http://localhost:3000/dashboard](http://localhost:3000/dashboard) with your browser to see the result.

You can start editing the page by modifying the files. The page auto-updates as you edit the file.

Execute `npm run lint` for static code analysis and `npm run format` to format the code with `prettier`.
