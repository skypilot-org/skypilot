function response(data, { status = 200, headers = {} } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => headers[name] ?? null },
    json: async () => data,
  };
}

const SYNC = { 'X-SkyPilot-Sync': '1' };

function answered(value) {
  return response(
    { status: 'SUCCEEDED', return_value: JSON.stringify(value) },
    { headers: SYNC }
  );
}

const queued = (id) =>
  response(null, { headers: { 'X-Skypilot-Request-ID': id } });

// A fresh module per test, so each sees its own /api/health.
function loadWithHealth(health) {
  global.fetch.mockReset();
  global.fetch.mockResolvedValue(response(health));
  let syncCalls;
  jest.isolateModules(() => {
    syncCalls = require('@/data/connectors/syncCalls');
  });
  return syncCalls;
}

describe('sendRequest', () => {
  test('sends the header when the server advertises sync calls', async () => {
    const { sendRequest } = loadWithHealth({ sync_call_max_limit: 100 });
    const send = jest.fn(async () => answered([]));
    await sendRequest(send);
    expect(send).toHaveBeenCalledWith(SYNC);
  });

  test('sends no header to a server that answers no sync calls', async () => {
    const { sendRequest } = loadWithHealth({ api_version: '72' });
    const send = jest.fn(async () => queued('r'));
    await sendRequest(send);
    expect(send).toHaveBeenCalledWith({});
  });

  test('sends no header for a limit above the maximum or none', async () => {
    const { sendRequest } = loadWithHealth({ sync_call_max_limit: 100 });
    const send = jest.fn(async () => queued('r'));
    await sendRequest(send, { limit: 500 });
    await sendRequest(send, { limit: null });
    await sendRequest(send, { sync: false });
    expect(send.mock.calls).toEqual([[{}], [{}], [{}]]);
    await sendRequest(send, { limit: 100 });
    expect(send).toHaveBeenLastCalledWith(SYNC);
  });

  test('sends a refused sync call again without the header', async () => {
    const { sendRequest } = loadWithHealth({ sync_call_max_limit: 100 });
    const send = jest
      .fn()
      .mockResolvedValueOnce(
        response({ detail: 'needs limit' }, { status: 400, headers: SYNC })
      )
      .mockResolvedValueOnce(queued('r'));
    const got = await sendRequest(send);
    expect(send.mock.calls).toEqual([[SYNC], [{}]]);
    expect(got.headers.get('X-Skypilot-Request-ID')).toBe('r');
  });

  test('retries a busy server after Retry-After', async () => {
    const { sendRequest } = loadWithHealth({ sync_call_max_limit: 100 });
    const busy = response(null, {
      status: 429,
      headers: { ...SYNC, 'Retry-After': '0' },
    });
    const send = jest
      .fn()
      .mockResolvedValueOnce(busy)
      .mockResolvedValueOnce(answered([1]));
    const got = await sendRequest(send);
    expect(send).toHaveBeenCalledTimes(2);
    expect(got.status).toBe(200);
  });
});

describe('fetchResult', () => {
  test('returns a sync answer without reading /api/get', async () => {
    const { fetchResult } = loadWithHealth({ sync_call_max_limit: 100 });
    const answer = answered({ a: 1 });
    const get = jest.fn();
    await expect(
      fetchResult('/workspaces', async () => answer, get)
    ).resolves.toBe(answer);
    expect(get).not.toHaveBeenCalled();
  });

  test('reads the result of a background request from /api/get', async () => {
    const { fetchResult } = loadWithHealth({});
    const result = response({ status: 'SUCCEEDED', return_value: '{}' });
    const get = jest.fn(async () => result);
    await expect(
      fetchResult('/workspaces', async () => queued('req-9'), get)
    ).resolves.toBe(result);
    expect(get).toHaveBeenCalledWith('/api/get?request_id=req-9');
  });
});

describe('fetchListInPages', () => {
  const names = ['a', 'b', 'c', 'd', 'e'];
  const byName = { cursorOf: (row) => row.name };

  function pages({ ignoreCursor = false, queue = false } = {}) {
    return jest.fn(async (after, size) => {
      if (queue) return queued('r');
      const rows = names
        .filter((n) => ignoreCursor || after === null || n > after)
        .slice(0, size)
        .map((name) => ({ name }));
      return answered(rows);
    });
  }

  test('follows the cursor until an empty page', async () => {
    const { fetchListInPages } = loadWithHealth({});
    const sendPage = pages();
    const rows = await fetchListInPages({
      path: '/volumes',
      sendPage,
      pageSize: 2,
      ...byName,
    });
    expect(rows.map((r) => r.name)).toEqual(names);
    expect(sendPage.mock.calls).toEqual([
      [null, 2],
      ['b', 2],
      ['d', 2],
      ['e', 2],
    ]);
  });

  test('pages in the order of the server collation', async () => {
    // A database collation that ignores case orders C after b, while byte
    // order puts it before.
    const { fetchListInPages } = loadWithHealth({});
    const sorted = ['a', 'b', 'C', 'd'];
    const sendPage = jest.fn(async (after, size) => {
      const start = after === null ? 0 : sorted.indexOf(after) + 1;
      return answered(
        sorted.slice(start, start + size).map((name) => ({ name }))
      );
    });
    const rows = await fetchListInPages({
      path: '/volumes',
      sendPage,
      pageSize: 2,
      ...byName,
    });
    expect(rows.map((r) => r.name)).toEqual(sorted);
  });

  test('gives up when a page repeats a row', async () => {
    const { fetchListInPages } = loadWithHealth({});
    await expect(
      fetchListInPages({
        path: '/volumes',
        sendPage: pages({ ignoreCursor: true }),
        pageSize: 2,
        ...byName,
      })
    ).resolves.toBeNull();
  });

  test('gives up when a page is not answered in its response', async () => {
    const { fetchListInPages } = loadWithHealth({});
    await expect(
      fetchListInPages({
        path: '/volumes',
        sendPage: pages({ queue: true }),
        pageSize: 2,
        ...byName,
      })
    ).resolves.toBeNull();
  });

  test('throws the error of a failed page', async () => {
    const { fetchListInPages } = loadWithHealth({});
    const failed = response(
      { detail: { error: JSON.stringify({ message: 'boom' }) } },
      { status: 500, headers: SYNC }
    );
    await expect(
      fetchListInPages({
        path: '/volumes',
        sendPage: async () => failed,
        pageSize: 2,
        ...byName,
      })
    ).rejects.toThrow('/volumes');
  });
});

describe('syncPageSize', () => {
  test('pages only a server with sync calls and the list cursor', async () => {
    let mod = loadWithHealth({ sync_call_max_limit: 100, api_version: '72' });
    await expect(mod.syncPageSize(72)).resolves.toBe(100);
    mod = loadWithHealth({ sync_call_max_limit: 100, api_version: '71' });
    await expect(mod.syncPageSize(72)).resolves.toBeNull();
    mod = loadWithHealth({ api_version: '72' });
    await expect(mod.syncPageSize(72)).resolves.toBeNull();
  });
});
