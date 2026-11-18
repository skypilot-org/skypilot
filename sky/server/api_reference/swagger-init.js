'use strict';

// Headers that plugins ask every client to send, besides the API version ones.
const REQUEST_HEADERS = {{request_headers}};

window.ui = SwaggerUIBundle({
  url: '/api-reference/openapi.json',
  dom_id: '#swagger-ui',
  deepLinking: true,
  queryConfigEnabled: false,
  validatorUrl: null,
  requestInterceptor: (request) => {
    if (request.loadSpec) {
      return request;
    }
    const info = window.ui.specSelectors.specJson().get('info');
    // The headers guide.md asks every client to send.
    const headers = {
      'X-SkyPilot-API-Version': String(info.get('x-skypilot-api-version')),
      'X-SkyPilot-Version': info.get('version'),
      ...REQUEST_HEADERS,
    };
    for (const [name, value] of Object.entries(headers)) {
      if (!(name in request.headers)) {
        request.headers[name] = value;
      }
    }
    return request;
  },
});
