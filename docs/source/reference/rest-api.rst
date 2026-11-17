.. _rest-api:

REST API
========

The :ref:`CLI <cli>` and the :ref:`Python SDK <pythonapi>` talk to the SkyPilot API server over its REST API. You can call the REST API directly when neither fits, for example from a language other than Python, or from a tool that can only make HTTP requests.

Every API server describes its own API at these paths:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Path
     - Content
   * - ``/api-reference/``
     - Interactive reference (Swagger UI). Browse the operations and try them against the server.
   * - ``/api-reference/openapi.json``
     - OpenAPI document of the public operations.
   * - ``/api-reference/guide.md``
     - Client guide: authentication, the headers to send, background requests, errors, retries and versioning.
   * - ``/.well-known/api-catalog``
     - API catalog (`RFC 9727 <https://www.rfc-editor.org/rfc/rfc9727>`__) that links to the three above.

They need the same authentication as the rest of the API, and users with the ``viewer`` role can read them. ``GET /api/health`` needs no authentication and points to the catalog with the header ``Link: </.well-known/api-catalog>; rel="api-catalog"``. ``/docs`` and ``/redoc`` redirect to ``/api-reference/``.

The document and the guide describe the server that serves them, so read them from your own API server rather than from another deployment.

Authentication
--------------

On an API server with authentication, send a :ref:`service account <service-accounts>` token as a bearer token. A local API server started with ``sky api start`` needs no token.

.. code-block:: console

   $ export ENDPOINT=https://skypilot.example.com
   $ export TOKEN=sky_...
   $ curl -H "Authorization: Bearer $TOKEN" "$ENDPOINT/api-reference/guide.md"

Background requests
-------------------

Most operations run as background requests, as in the Python SDK. They answer right away and put the request ID in the ``X-Skypilot-Request-ID`` response header. The OpenAPI document marks them with ``x-skypilot-async: true``. ``GET /api/get?request_id=<id>`` waits for the request to finish and returns its result. Its ``return_value`` field is a JSON-encoded string.

For example, to list clusters:

.. code-block:: console

   $ API_VERSION=$(curl -s -H "Authorization: Bearer $TOKEN" "$ENDPOINT/api/health" | jq -r .api_version)
   $ VERSION=$(curl -s -H "Authorization: Bearer $TOKEN" "$ENDPOINT/api/health" | jq -r .version)
   $ REQUEST_ID=$(curl -s -o /dev/null -D - -X POST "$ENDPOINT/status" \
         -H "Authorization: Bearer $TOKEN" \
         -H "X-SkyPilot-API-Version: $API_VERSION" -H "X-SkyPilot-Version: $VERSION" \
         -H 'Content-Type: application/json' \
         -d '{"include_credentials": false, "include_handle": false}' \
       | awk 'tolower($1) == "x-skypilot-request-id:" {print $2}' | tr -d '\r')
   $ curl -s "$ENDPOINT/api/get?request_id=$REQUEST_ID" \
         -H "Authorization: Bearer $TOKEN" \
         -H "X-SkyPilot-API-Version: $API_VERSION" -H "X-SkyPilot-Version: $VERSION" \
       | jq '{status, clusters: (.return_value | fromjson)}'
   {
     "status": "SUCCEEDED",
     "clusters": []
   }

Send the ``X-SkyPilot-API-Version`` and ``X-SkyPilot-Version`` headers on every request: without them, the server treats the request as coming from a very old client. The client guide at ``/api-reference/guide.md`` covers these headers, request logs, cancellation, errors, retries and versioning in full.

.. note::

   Some response fields, such as cluster handles and resources, are base64-encoded Python pickles that only the SkyPilot Python client can read. Leave them out where an operation allows it, such as ``"include_handle": false`` on ``POST /status``.
