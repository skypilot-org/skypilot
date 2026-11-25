'use client';

import { getErrorMessageFromResponse } from '@/data/utils';
import {
  API_VERSION_HEADER,
  CLIENT_API_VERSION,
  CLIENT_VERSION,
  ENDPOINT,
  VERSION_HEADER,
} from './constants';

// Sync calls (sky/server/constants.py SYNC_CALL_HEADER). A request sent with
// this header set to '1' may be answered with its result. A response carrying
// it with value '1' was answered that way: 200 and 500 have the /api/get body,
// 400 means the arguments do not allow a sync call, and 429 means the server
// is busy (see Retry-After). Other responses carry a request id as usual.
export const SYNC_CALL_HEADER = 'X-SkyPilot-Sync';
const BUSY_RETRIES = 5;
const MAX_RETRY_AFTER_MS = 5000;

// The API version whose servers take `limit` and a cursor on the cluster,
// volume, storage and recipe lists.
export const MIN_LIST_CURSOR_API_VERSION = 72;

let syncCallInfoPromise = null;

// What /api/health advertises about sync calls: the largest `limit` the server
// answers as a sync call (null when it answers none) and its API version.
export function getSyncCallInfo() {
  if (!syncCallInfoPromise) {
    syncCallInfoPromise = (async () => {
      try {
        const response = await fetch(
          `${window.location.origin}${ENDPOINT}/api/health`,
          {
            headers: {
              [API_VERSION_HEADER]: CLIENT_API_VERSION,
              [VERSION_HEADER]: CLIENT_VERSION,
            },
          }
        );
        if (!response.ok) return { maxLimit: null, apiVersion: null };
        const health = await response.json();
        const maxLimit = health?.sync_call_max_limit;
        const apiVersion = Number(health?.api_version);
        return {
          maxLimit:
            Number.isInteger(maxLimit) && maxLimit >= 1 ? maxLimit : null,
          apiVersion: Number.isFinite(apiVersion) ? apiVersion : null,
        };
      } catch {
        return { maxLimit: null, apiVersion: null };
      }
    })();
  }
  return syncCallInfoPromise;
}

function isMarked(response) {
  return response?.headers?.get(SYNC_CALL_HEADER) === '1';
}

// Whether the server answered the request with its result (the /api/get body).
export function isSyncAnswer(response) {
  return (
    isMarked(response) && (response.status === 200 || response.status === 500)
  );
}

function retryAfterMs(response) {
  const seconds = Number(response.headers.get('Retry-After'));
  const ms = Number.isFinite(seconds) && seconds >= 0 ? seconds * 1000 : 1000;
  return Math.min(ms, MAX_RETRY_AFTER_MS);
}

// Sends a request through send(headers), as a sync call when the server
// answers them. A request that takes a `limit` passes it: the server answers
// it as a sync call only with a limit between 1 and its maximum. A sync call
// the server refuses is sent again without the header, and so is one the
// server stays too busy for after BUSY_RETRIES tries paced by Retry-After.
export async function sendRequest(send, { sync = true, limit } = {}) {
  if (!sync) return send({});
  const { maxLimit } = await getSyncCallInfo();
  if (
    maxLimit === null ||
    (limit !== undefined &&
      !(Number.isInteger(limit) && limit >= 1 && limit <= maxLimit))
  ) {
    return send({});
  }
  for (let attempt = 0; attempt < BUSY_RETRIES; attempt++) {
    const response = await send({ [SYNC_CALL_HEADER]: '1' });
    if (!isMarked(response)) return response;
    if (response.status === 400) return send({});
    if (response.status !== 429) return response;
    await new Promise((resolve) => setTimeout(resolve, retryAfterMs(response)));
  }
  return send({});
}

// Sends a request through send(headers) and returns the response with its
// result in the /api/get shape: the response itself when the server answered
// it as a sync call, otherwise the /api/get response read through get(path).
export async function fetchResult(path, send, get, options = {}) {
  const response = await sendRequest(send, options);
  if (isSyncAnswer(response)) return response;
  if (!response.ok) {
    const errorMessage = await getErrorMessageFromResponse(response);
    throw new Error(
      `Initial API request ${path} failed with status ${response.status}, error: ${errorMessage}`
    );
  }
  const id = response.headers.get('X-Skypilot-Request-ID');
  if (!id) {
    throw new Error(`No request ID received from server for ${path}`);
  }
  return get(`/api/get?request_id=${id}`);
}

// The page size to fetch a list in as sync calls, or null to fetch it in one
// call: the server answers sync calls and takes the list's cursor.
export async function syncPageSize(minApiVersion) {
  const { maxLimit, apiVersion } = await getSyncCallInfo();
  if (maxLimit === null || apiVersion === null || apiVersion < minApiVersion) {
    return null;
  }
  return maxLimit;
}

// Fetches a list as sync calls of up to pageSize rows each.
//
// sendPage(cursor, size) sends one page and returns its response; the first
// cursor is null. cursorOf(row) is the cursor after that row, and
// follows(row, cursor) is whether the row comes after that cursor. The list
// ends at the first empty page: the server can drop rows after applying its
// limit, so a short page does not end it.
//
// Returns the rows, or null when a page is not answered in its response or
// holds a row that does not come after its cursor; the caller then fetches the
// list in one call. Throws the error of a page that failed.
export async function fetchListInPages({
  path,
  sendPage,
  pageSize,
  cursorOf,
  follows,
}) {
  const rows = [];
  let cursor = null;
  for (;;) {
    const response = await sendPage(cursor, pageSize);
    if (!isSyncAnswer(response)) return null;
    if (response.status !== 200) {
      const errorMessage = await getErrorMessageFromResponse(response);
      throw new Error(
        `API request to get ${path} result failed with status ${response.status}, error: ${errorMessage}`
      );
    }
    const data = await response.json();
    const page = data.return_value ? JSON.parse(data.return_value) : [];
    if (!Array.isArray(page)) return null;
    if (cursor !== null && !page.every((row) => follows(row, cursor))) {
      return null;
    }
    if (page.length === 0) return rows;
    rows.push(...page);
    cursor = cursorOf(page[page.length - 1]);
  }
}
