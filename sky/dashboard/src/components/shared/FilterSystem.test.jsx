import {
  evaluateCondition,
  updateFiltersByURLParams,
} from '@/components/shared/FilterSystem';

const PROPERTY_MAP = new Map([
  ['status', 'Status'],
  ['cluster', 'Cluster'],
  ['user', 'User'],
  ['labels', 'Labels'],
]);

const routerWith = (query) => ({ query });

describe('updateFiltersByURLParams', () => {
  it('decodes a single filter', () => {
    const filters = updateFiltersByURLParams(
      routerWith({ property: 'status', operator: ':', value: 'UP' }),
      PROPERTY_MAP
    );
    expect(filters).toEqual([
      { property: 'Status', operator: ':', value: 'UP' },
    ]);
  });

  it('decodes multiple filters, pairing each array by index', () => {
    const filters = updateFiltersByURLParams(
      routerWith({
        property: ['status', 'user'],
        operator: [':', ':'],
        value: ['UP', 'alice'],
      }),
      PROPERTY_MAP
    );
    expect(filters).toEqual([
      { property: 'Status', operator: ':', value: 'UP' },
      { property: 'User', operator: ':', value: 'alice' },
    ]);
  });

  it('returns no filters when the URL carries none', () => {
    expect(updateFiltersByURLParams(routerWith({}), PROPERTY_MAP)).toEqual([]);
  });

  // A URL is user-editable input. A property this page cannot filter on must
  // not survive as a filter with no property: that used to render a chip
  // labeled `undefined`, silently turn the filter into a full-text search, and
  // then throw on the next chip removal.
  it('drops a property the page does not know about', () => {
    const filters = updateFiltersByURLParams(
      routerWith({
        property: ['bogus', 'status'],
        operator: [':', ':'],
        value: ['x', 'UP'],
      }),
      PROPERTY_MAP
    );
    expect(filters).toEqual([
      { property: 'Status', operator: ':', value: 'UP' },
    ]);
  });

  it('drops an empty property', () => {
    const filters = updateFiltersByURLParams(
      routerWith({ property: '', operator: ':', value: 'alice' }),
      PROPERTY_MAP
    );
    expect(filters).toEqual([]);
  });

  it('keeps a labels filter, which the clusters page offers', () => {
    const filters = updateFiltersByURLParams(
      routerWith({ property: 'labels', operator: ':', value: 'team:ml' }),
      PROPERTY_MAP
    );
    expect(filters).toEqual([
      { property: 'Labels', operator: ':', value: 'team:ml' },
    ]);
  });
});

describe('evaluateCondition', () => {
  const item = { status: 'UP', cluster: 'train-a100', user: 'alice' };

  it('matches a substring with the : operator', () => {
    expect(
      evaluateCondition(item, {
        property: 'Cluster',
        operator: ':',
        value: 'train',
      })
    ).toBe(true);
  });

  it('requires equality with the = operator', () => {
    expect(
      evaluateCondition(item, {
        property: 'Cluster',
        operator: '=',
        value: 'train',
      })
    ).toBe(false);
    expect(
      evaluateCondition(item, {
        property: 'Cluster',
        operator: '=',
        value: 'train-a100',
      })
    ).toBe(true);
  });

  it('skips a filter with no value', () => {
    expect(
      evaluateCondition(item, { property: 'Cluster', operator: ':', value: '' })
    ).toBe(true);
  });

  // Guard, not a code path: the decoders drop these. It must skip the filter
  // rather than throw on the property lookup.
  it('skips a filter with no property instead of throwing', () => {
    expect(() =>
      evaluateCondition(item, {
        property: undefined,
        operator: ':',
        value: 'alice',
      })
    ).not.toThrow();
    expect(
      evaluateCondition(item, {
        property: undefined,
        operator: ':',
        value: 'alice',
      })
    ).toBe(true);
  });
});
