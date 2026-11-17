# SkyPilot API client guide

This guide explains how to call this SkyPilot API server from your own code.
The OpenAPI document at `/api-reference/openapi.json` lists every public
operation this server supports. If an operation is listed, this server has it.
To browse it and try calls in a browser, open `/api-reference/`.

## Authentication

Authenticate every request with a service account token. An admin creates one in
the dashboard under **Users → Service Accounts**. Send it as a bearer token:

```
Authorization: Bearer sky_xxxxxxxxxxxxxxxxxxxxxxxx
```

The server identifies the caller from the token. A token acts with its service
account's role and workspace access. A missing or invalid token gets `401`; an
operation the role does not allow gets `403`.

## Headers to send on every request

```
X-SkyPilot-API-Version: {{api_version}}
X-SkyPilot-Version: <the "version" field of GET /api/health>
{{slot:request_headers}}
```

- `X-SkyPilot-API-Version` and `X-SkyPilot-Version` tell the server which API
  version your client was written against. Send both. A request without them is
  treated as coming from a very old client: the server skips version checks,
  turns off behavior that newer clients opt into, and may drop newer fields from
  responses. `{{api_version}}` is this server's current API version; it is also
  the `api_version` field of `GET /api/health`.
{{slot:request_header_notes}}

To act in a workspace other than your default one, add
`"override_skypilot_config": {"active_workspace": "<workspace name>"}` to the
body.

{{slot:python_only_fields}}

## Two kinds of operations

Some operations answer with their result directly. Others submit a request that
the server runs in the background. The OpenAPI document marks the second kind
with `x-skypilot-async: true`.

### Background requests

A background request answers `200` and puts the request ID in the
`X-Skypilot-Request-ID` response header. The body is `null` unless the
operation documents one. Get the result with:

```
GET /api/get?request_id=<request ID>
```

`/api/get` waits until the request finishes, then returns a request payload:

- `status` is the final status, such as `SUCCEEDED` or `FAILED`.
- `return_value` is a JSON-encoded string. Parse it a second time to get the
  result.
- On failure the server answers `500`. The body's `detail` is the same payload,
  and `detail.error` is a JSON-encoded string with `type` (the exception class
  name) and `message`. Ignore its `object` field: it is a Python pickle.

If the connection drops while you wait, call `/api/get` again with the same ID.
Waiting is safe to repeat. A request ID prefix works too, as long as it matches
one request.

Other request operations:

- `GET /api/stream?request_id=<id>&format=plain` streams the request's log as
  plain text until it finishes. Use it for long operations such as `/launch`.
- `POST /api/cancel` with `{"request_ids": ["<id>"]}` cancels requests.
- `GET /api/status?request_ids=<id>` returns a request's status without waiting.

{{slot:inline_reads}}

## Errors and retries

| Status | Meaning | What to do |
|---|---|---|
{{slot:error_rows}}
| `503` | The server is busy, restarting, or temporarily cannot check credentials. | Retry with exponential backoff, starting at 1 second and capping at 30 seconds. Honor `Retry-After` when it is present. The request was not accepted, so resubmitting does not run it twice. |
| `500` from `/api/get` with error type `RequestInterruptedError` | A server restart interrupted the request. It will not resume. | Submit the original request again. |
| `400` with `"error": "version_mismatch"` | The API version you send is older than the oldest version this server accepts. | Review the API changes and raise the version you send. |
| `401` / `403` | The token is missing or invalid, or the role does not allow the operation. | Fix the token or the service account's role. Do not retry. |
| `404` from `/api/get` | No request matches the ID. | Do not retry. |

Do not retry other `4xx` responses unchanged.

## Reading efficiently

- Do not poll full lists. Ask for what you need and poll no more often than
  every 10 seconds.
- `POST /status`: pass `cluster_names` when you track specific clusters, and set
  `"include_credentials": false` and `"include_handle": false` unless you need
  SSH credentials or the cluster handle. The defaults return both, which makes
  responses large and puts SSH keys in them. Keep `"refresh": "NONE"` (the
  default) for polling; `AUTO` and `FORCE` query the cloud or cluster.
- `POST /jobs/queue/v2`: set `limit` and `page`, `"skip_finished": true` when
  you only need active jobs, `fields` to return only the columns you use, and
  `submitted_after` to read only new jobs.
{{slot:efficient_reads}}

## Files on your machine

The server cannot read files on your machine. A task whose `workdir` or
`file_mounts` point to local paths needs those files uploaded first, which the
SkyPilot clients do with the `/upload` operations. When you call the API
directly, point them at git repositories or cloud storage instead.

## Versioning

- The server's API version is in the `api_version` field of `GET /api/health`,
  in the `X-SkyPilot-API-Version` response header, and in this document's
  `info.x-skypilot-api-version`.
- The server accepts clients whose API version is at least
  `info.x-skypilot-min-compatible-api-version`. SkyPilot keeps adjacent minor
  releases compatible: new request fields have defaults, and a server ignores
  request fields it does not know.
- Raise the version you send after each minor release of the server, once you
  have checked the changes. Older versions keep working until they fall below
  the minimum.

## Discovery

`GET /.well-known/api-catalog` (RFC 9727) links to this guide and to the OpenAPI
document. All three need the same bearer token as the rest of the API.
`GET /api/health`, which needs no token, points to the catalog with the header
`Link: </.well-known/api-catalog>; rel="api-catalog"`.
