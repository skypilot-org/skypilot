# Calling the REST API

Use the API server's REST API when the `sky` CLI and the Python SDK do not fit:

- The environment cannot install or run the SkyPilot Python client (no Python or pip, a locked-down sandbox, a container you cannot change).
- The caller is not Python (a TypeScript service, a Go tool, a workflow engine's HTTP step).
- You can only make HTTP requests (an HTTP tool, `curl` only).

Prefer the CLI or SDK whenever they work: they handle uploads of local files, streaming and result decoding for you.

## Discover the API from the server

Every API server describes itself. Read these from the server you will call, not from memory or another deployment, because operations and fields differ between versions and plugins:

| Path | What it is |
|------|------------|
| `/api-reference/guide.md` | Client guide: auth, headers to send, background requests, errors and retries, versioning. **Read this first.** |
| `/api-reference/openapi.json` | OpenAPI document of the public operations, with request and response schemas. |
| `/api-reference/` | Swagger UI, for a human to browse and try calls. |
| `/.well-known/api-catalog` | RFC 9727 catalog linking to the three above. |

`GET /api/health` needs no auth and returns `Link: </.well-known/api-catalog>; rel="api-catalog"`. The other paths need the same auth as the API.

Ask the user for the API server endpoint. A local server (`sky api start`) listens on `http://127.0.0.1:46580` and needs no token.

## Authenticate

On a server with authentication, send a service account token: `Authorization: Bearer sky_...`. An admin creates one in the dashboard under **Users → Service Accounts**. Ask the user for it, keep it in an environment variable, and never print or log it.

## The request pattern

1. Send `X-SkyPilot-API-Version` and `X-SkyPilot-Version` on every request. Take the values from the `api_version` and `version` fields of `GET /api/health`. Without them the server treats you as a very old client.
2. Operations marked `x-skypilot-async: true` in the OpenAPI document run in the background. They answer `200` with the request ID in the `X-Skypilot-Request-ID` response header.
3. `GET /api/get?request_id=<id>` waits for the request to finish and returns its payload. `return_value` is a JSON-encoded string: parse it a second time. A failure answers `500` with the payload under `detail`.
4. For long operations such as `POST /launch`, follow the log with `GET /api/stream?request_id=<id>&format=plain` instead of waiting silently.

```bash
API_VERSION=$(curl -s -H "Authorization: Bearer $TOKEN" "$ENDPOINT/api/health" | jq -r .api_version)
VERSION=$(curl -s -H "Authorization: Bearer $TOKEN" "$ENDPOINT/api/health" | jq -r .version)
REQUEST_ID=$(curl -s -o /dev/null -D - -X POST "$ENDPOINT/status" \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-SkyPilot-API-Version: $API_VERSION" -H "X-SkyPilot-Version: $VERSION" \
    -H 'Content-Type: application/json' \
    -d '{"include_credentials": false, "include_handle": false}' \
  | awk 'tolower($1) == "x-skypilot-request-id:" {print $2}' | tr -d '\r')
curl -s "$ENDPOINT/api/get?request_id=$REQUEST_ID" \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-SkyPilot-API-Version: $API_VERSION" -H "X-SkyPilot-Version: $VERSION" \
  | jq '{status, clusters: (.return_value | fromjson)}'
```

## Pitfalls

- Some response fields (cluster handles, resources) are base64-encoded Python pickles. You cannot read them without the Python client; set `"include_handle": false` on `POST /status` and ignore such fields elsewhere.
- The server cannot read files on the caller's machine. A task whose `workdir` or `file_mounts` point to local paths fails over plain HTTP; point them at a git repository or cloud storage instead.
- Do not poll full lists in a tight loop. Filter (`cluster_names`, job filters), and wait on a specific request with `/api/get` or `/api/stream`.
- Follow the guide's retry rules: retry `503` with backoff, resubmit on `RequestInterruptedError`, do not retry `401`, `403` or other `4xx` responses unchanged.
