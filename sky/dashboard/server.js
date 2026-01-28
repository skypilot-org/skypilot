import { createProxyMiddleware } from 'http-proxy-middleware';
import express, { text } from 'express';
import next from 'next';
import open from 'open';

const dev = process.env.NODE_ENV !== 'production';
const app = next({ dev });
const handle = app.getRequestHandler();

async function getOAuthToken(remote_server, local_port) {
  return new Promise(async (resolve, reject) => {
    try {
      const server = await launchCallbackServer(local_port, (token) => {
        resolve(token);
        server.close(() => {
          console.log('OAuth callback server closed.');
        });
      });

      server.on('error', reject);

      await openBrowser(remote_server, local_port);
    } catch (e) {
      console.error('Error retrieving OAuth token:', e);
      reject(e);
    }
  });
}

async function launchCallbackServer(local_port, callback) {
  const app = express();

  app.post('/', text(), (req, res) => {
    try {
      const buff = Buffer.from(req.body, 'base64');
      const json = JSON.parse(buff.toString('utf-8'));
      const token = json?.cookies?._oauth2_proxy;

      res.status(200).setHeader('Access-Control-Allow-Origin', '*').end();

      callback(token);
    } catch (e) {
      console.error('Error processing OAuth callback:', e);
      res
        .status(400)
        .setHeader('Access-Control-Allow-Origin', '*')
        .contentType('application/json')
        .send(JSON.stringify({ error: e.message }))
        .end();
    }
  });

  app.all('*', (_, res) => {
    console.log('Received invalid request on OAuth callback server.');
    res
      .status(400)
      .setHeader('Access-Control-Allow-Origin', '*')
      .contentType('application/json')
      .send(JSON.stringify({ error: 'Invalid request' }))
      .end();
  });

  const server = app.listen(local_port, (err) => {
    if (err) throw err;
    console.log(`OAuth callback server listening on port ${local_port}`);
    console.log(`Please complete the OAuth flow in your browser.`);
  });

  return server;
}

async function openBrowser(endpoint, local_port) {
  const token_url = `${endpoint}/token?local_port=${local_port}`;

  return open(token_url)
    .then(() => {
      console.log(`Opened browser to ${token_url} for OAuth flow.`);
    })
    .catch((err) => {
      console.error('Failed to open browser for OAuth flow:', err);
    });
}

let oauth_token = null;

if (Boolean(process.env.SKYPILOT_OAUTH_ENABLED)) {
  oauth_token = await getOAuthToken(
    process.env.SKYPILOT_API_SERVER_ENDPOINT || 'http://localhost:46580',
    8000
  ).catch((e) => {
    console.error('Failed to get OAuth token:', e);
    return null;
  });
}

app
  .prepare()
  .then(() => {
    const server = express();

    if (process.env.SKYPILOT_ACCESS_TOKEN) {
      console.log('Using SKYPILOT_ACCESS_TOKEN for authentication.');
      server.use((req, _, next) => {
        req.headers['X-Skypilot-Auth-Mode'] = 'token';
        req.headers['Authorization'] =
          `Bearer ${process.env.SKYPILOT_ACCESS_TOKEN}`;
        next();
      });
    }

    if (oauth_token) {
      console.log('Using OAuth token for authentication.');
      server.use((req, _, next) => {
        // Parse existing cookies
        const cookies = req.headers.cookie
          ? req.headers.cookie.split(';').map((c) => c.trim())
          : [];
        // Remove any existing _oauth2_proxy cookie
        const filteredCookies = cookies.filter(
          (c) => !c.startsWith('_oauth2_proxy=')
        );
        // Add/replace _oauth2_proxy cookie
        filteredCookies.push(`_oauth2_proxy=${oauth_token}`);
        req.headers.cookie = filteredCookies.join('; ');
        next();
      });
    }

    // Proxy API requests
    server.use(
      '/internal/dashboard',
      createProxyMiddleware({
        target:
          process.env.SKYPILOT_API_SERVER_ENDPOINT || 'http://localhost:46580',
        changeOrigin: true,
        pathRewrite: {
          '^/internal/dashboard': '', // remove /internal/dashboard prefix when forwarding to the target
        },
      })
    );

    // Proxy Grafana requests
    server.use(
      '/grafana',
      createProxyMiddleware({
        target: `${process.env.SKYPILOT_API_SERVER_ENDPOINT || 'http://localhost:46580'}/grafana`,
        changeOrigin: true,
      })
    );

    server.all('*', (req, res) => {
      return handle(req, res);
    });

    server.listen(3000, (err) => {
      if (err) throw err;
      console.log('> Ready on http://localhost:3000/dashboard');
    });
  })
  .catch((err) => {
    console.error('Error during app preparation:', err);
  });

console.log('Server script is running...');
