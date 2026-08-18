import {
  buildQueryString,
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
