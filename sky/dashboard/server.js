const { createProxyMiddleware } = require('http-proxy-middleware');
const express = require('express');
const next = require('next');

const dev = process.env.NODE_ENV !== 'production';
const app = next({ dev });
const handle = app.getRequestHandler();

app
  .prepare()
  .then(() => {
    const server = express();

    if (process.env.SKYPILOT_ACCESS_TOKEN) {
      server.use((req, _, next) => {
        req.headers['X-Skypilot-Auth-Mode'] = 'token';
        req.headers['Authorization'] =
          `Bearer ${process.env.SKYPILOT_ACCESS_TOKEN}`;
        next();
      });
    }

    if (process.env.SKYPILOT_OAUTH_COOKIE) {
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
        filteredCookies.push(
          `_oauth2_proxy=${process.env.SKYPILOT_OAUTH_COOKIE || ''}`
        );
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
