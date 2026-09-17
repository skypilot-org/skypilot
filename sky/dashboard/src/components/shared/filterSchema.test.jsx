import {
  buildQueryString,
  hrefWithQueryKey,
  filtersToQuery,
  hasLegacyFilters,
  legacyFiltersToQuery,
  queryToFilters,
  withoutLegacyKeys,
} from '@/components/shared/filterSchema';

const SCHEMA = [
  { key: 'status', label: 'Status', kind: 'enum', multi: true },
  { key: 'cluster', label: 'Cluster', kind: 'text' },
  { key: 'user', label: 'User', kind: 'text' },
  { key: 'labels', label: 'Labels', kind: 'kv', multi: 'repeat' },
];

// Properties whose URL key was renamed, so the old `property=` value is
// neither the new key nor the lowercased label.
const RENAMED_SCHEMA = [
  { key: 'gpu', label: 'GPU', kind: 'text', legacyKeys: ['gpu type'] },
  { key: 'userId', label: 'User ID', kind: 'text', legacyKeys: ['user id'] },
];

const chip = (property, value) => ({ property, operator: ':', value });

describe('filtersToQuery', () => {
  it('names each filter after its schema key', () => {
    expect(filtersToQuery(SCHEMA, [chip('User', 'alice')])).toEqual({
      user: 'alice',
    });
  });

  it('joins several enum values with a comma', () => {
    expect(
      filtersToQuery(SCHEMA, [
        chip('Status', 'RUNNING'),
        chip('Status', 'STOPPED'),
      ])
    ).toEqual({ status: 'RUNNING,STOPPED' });
  });

  it('repeats the key for labels, whose values may contain commas', () => {
    expect(
      filtersToQuery(SCHEMA, [
        chip('Labels', 'team:ml'),
        chip('Labels', 'env:prod'),
      ])
    ).toEqual({ labels: ['team:ml', 'env:prod'] });
  });

  it('keeps one value for a text filter', () => {
    expect(
      filtersToQuery(SCHEMA, [chip('User', 'alice'), chip('User', 'bob')])
    ).toEqual({ user: 'bob' });
  });

  it('omits empty values entirely rather than writing a bare key', () => {
    expect(filtersToQuery(SCHEMA, [chip('User', '')])).toEqual({});
    expect(filtersToQuery(SCHEMA, [])).toEqual({});
  });
});

describe('queryToFilters', () => {
  it('round-trips a single filter', () => {
    expect(queryToFilters(SCHEMA, { user: 'alice' })).toEqual([
      chip('User', 'alice'),
    ]);
  });

  it('splits a comma list into one chip per value', () => {
    expect(queryToFilters(SCHEMA, { status: 'RUNNING,STOPPED' })).toEqual([
      chip('Status', 'RUNNING'),
      chip('Status', 'STOPPED'),
    ]);
  });

  it('reads a repeated key back as several chips', () => {
    expect(queryToFilters(SCHEMA, { labels: ['team:ml', 'env:prod'] })).toEqual(
      [chip('Labels', 'team:ml'), chip('Labels', 'env:prod')]
    );
  });

  it('does not split a text value on commas', () => {
    expect(queryToFilters(SCHEMA, { cluster: 'a,b' })).toEqual([
      chip('Cluster', 'a,b'),
    ]);
  });

  it('drops a key the schema does not know', () => {
    expect(queryToFilters(SCHEMA, { bogus: 'x', user: 'alice' })).toEqual([
      chip('User', 'alice'),
    ]);
  });

  it('drops blank and whitespace-only values', () => {
    expect(
      queryToFilters(SCHEMA, { user: '', status: 'RUNNING, ,STOPPED' })
    ).toEqual([chip('Status', 'RUNNING'), chip('Status', 'STOPPED')]);
  });

  it('survives a full round-trip', () => {
    const filters = [
      chip('Status', 'RUNNING'),
      chip('Status', 'STOPPED'),
      chip('User', 'alice'),
      chip('Labels', 'team:ml'),
    ];
    expect(queryToFilters(SCHEMA, filtersToQuery(SCHEMA, filters))).toEqual(
      filters
    );
  });
});

describe('legacy triple links', () => {
  it('recognises a legacy URL', () => {
    expect(hasLegacyFilters({ property: 'user' })).toBe(true);
    expect(hasLegacyFilters({ user: 'alice' })).toBe(false);
    expect(hasLegacyFilters({})).toBe(false);
  });

  it('translates a single triple', () => {
    expect(
      legacyFiltersToQuery(SCHEMA, {
        property: 'user',
        operator: ':',
        value: 'alice',
      })
    ).toEqual({ user: 'alice' });
  });

  it('matches the lowercased label when it differs from the key', () => {
    // Legacy links carried `filter.property` lowercased, which is the label,
    // not the key.
    expect(
      legacyFiltersToQuery(RENAMED_SCHEMA, {
        property: 'user id',
        operator: ':',
        value: 'hash-alice',
      })
    ).toEqual({ userId: 'hash-alice' });
  });

  it('matches a declared legacy spelling that is neither key nor label', () => {
    // The users page wrote `gpu type` for a property whose label is `GPU` and
    // whose key is now `gpu`; without `legacyKeys` such a link is dropped.
    expect(
      legacyFiltersToQuery(RENAMED_SCHEMA, {
        property: 'gpu type',
        operator: ':',
        value: 'A100',
      })
    ).toEqual({ gpu: 'A100' });
  });

  it('translates several triples, pairing arrays by index', () => {
    expect(
      legacyFiltersToQuery(SCHEMA, {
        property: ['user', 'status'],
        operator: [':', ':'],
        value: ['alice', 'RUNNING'],
      })
    ).toEqual({ user: 'alice', status: 'RUNNING' });
  });

  it('collapses two legacy chips on one enum into a comma list', () => {
    expect(
      legacyFiltersToQuery(SCHEMA, {
        property: ['status', 'status'],
        operator: [':', ':'],
        value: ['RUNNING', 'STOPPED'],
      })
    ).toEqual({ status: 'RUNNING,STOPPED' });
  });

  it('drops a legacy property the schema does not know', () => {
    expect(
      legacyFiltersToQuery(SCHEMA, {
        property: ['bogus', 'user'],
        operator: [':', ':'],
        value: ['x', 'alice'],
      })
    ).toEqual({ user: 'alice' });
  });

  it('ignores the operator, which no UI could ever vary', () => {
    expect(
      legacyFiltersToQuery(SCHEMA, {
        property: 'user',
        operator: '=',
        value: 'alice',
      })
    ).toEqual({ user: 'alice' });
  });

  it('strips the legacy keys and leaves the rest untouched', () => {
    expect(
      withoutLegacyKeys({
        property: 'user',
        operator: ':',
        value: 'alice',
        owner: 'all',
      })
    ).toEqual({ owner: 'all' });
  });
});

describe('buildQueryString', () => {
  it('keeps a comma readable instead of percent-encoding it', () => {
    expect(buildQueryString({ status: 'RUNNING,STOPPED' })).toBe(
      '?status=RUNNING,STOPPED'
    );
  });

  it('encodes characters that would otherwise break the query', () => {
    expect(buildQueryString({ user: 'a b&c=d' })).toBe('?user=a%20b%26c%3Dd');
  });

  it('repeats a key for array values', () => {
    expect(buildQueryString({ labels: ['team:ml', 'env:prod'] })).toBe(
      '?labels=team%3Aml&labels=env%3Aprod'
    );
  });

  it('returns an empty string when nothing is set', () => {
    expect(buildQueryString({})).toBe('');
    expect(buildQueryString({ user: '' })).toBe('');
  });
});

// Switching a tab is a same-page navigation that changes one query key. It must
// not drop the filter params the hook wrote straight to history, and must not
// percent-encode a comma list on the way through.
describe('hrefWithQueryKey', () => {
  it('keeps the other keys when setting one', () => {
    expect(
      hrefWithQueryKey(
        '/users',
        '?gpu=A100&role=admin',
        'tab',
        'service-accounts'
      )
    ).toBe('/users?gpu=A100&role=admin&tab=service-accounts');
  });

  it('keeps a comma list readable', () => {
    expect(
      hrefWithQueryKey('/volumes', '?status=READY,IN_USE', 'tab', 'buckets')
    ).toBe('/volumes?status=READY,IN_USE&tab=buckets');
  });

  it('removes the key when the value is dropped', () => {
    expect(
      hrefWithQueryKey(
        '/users',
        '?gpu=A100&tab=service-accounts',
        'tab',
        undefined
      )
    ).toBe('/users?gpu=A100');
  });

  it('returns a bare path when nothing is left', () => {
    expect(
      hrefWithQueryKey('/users', '?tab=service-accounts', 'tab', undefined)
    ).toBe('/users');
  });

  it('preserves a repeated key', () => {
    expect(
      hrefWithQueryKey('/clusters', '?labels=a%3D1&labels=b%3D2', 'tab', 'x')
    ).toBe('/clusters?labels=a%3D1&labels=b%3D2&tab=x');
  });
});
