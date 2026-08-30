import {
  evaluateCondition,
  filterData,
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

// The Infra column shows `Cloud (region-or-zone)`, but users reach for the
// `--infra` spec they type at the CLI. Both have to narrow the table.
describe('evaluateCondition on infra', () => {
  const infraFilter = (value) => ({
    property: 'Infra',
    operator: ':',
    value,
  });
  const slurmJob = {
    cloud: 'Slurm',
    region: 'prod-gpu',
    infra: 'Slurm (prod-gpu)',
    full_infra: 'Slurm (prod-gpu)',
  };
  const k8sJob = {
    cloud: 'Kubernetes',
    region: 'cluster-2',
    infra: 'Kubernetes (cluster-2)',
    full_infra: 'Kubernetes (cluster-2) (1xH100)',
  };
  const awsJob = {
    cloud: 'AWS',
    region: 'us-east-1',
    zone: 'us-east-1a',
    infra: 'AWS (us-east-1a)',
    full_infra: 'AWS (us-east-1a)',
  };

  it('matches a bare cloud', () => {
    expect(evaluateCondition(slurmJob, infraFilter('slurm'))).toBe(true);
    expect(evaluateCondition(k8sJob, infraFilter('kubernetes'))).toBe(true);
  });

  it('accepts k8s as an alias for kubernetes, as the CLI does', () => {
    expect(evaluateCondition(k8sJob, infraFilter('k8s'))).toBe(true);
    expect(evaluateCondition(k8sJob, infraFilter('k8s/cluster-2'))).toBe(true);
  });

  it('matches a cloud/region spec', () => {
    expect(evaluateCondition(slurmJob, infraFilter('slurm/prod-gpu'))).toBe(
      true
    );
    expect(evaluateCondition(k8sJob, infraFilter('kubernetes/cluster-2'))).toBe(
      true
    );
    expect(evaluateCondition(awsJob, infraFilter('aws/us-east-1'))).toBe(true);
  });

  it('narrows on a half-typed region, so the table filters as you type', () => {
    expect(evaluateCondition(slurmJob, infraFilter('slurm/'))).toBe(true);
    expect(evaluateCondition(slurmJob, infraFilter('slurm/prod'))).toBe(true);
  });

  it('still matches the region alone, as it did before the spec syntax', () => {
    expect(evaluateCondition(slurmJob, infraFilter('prod-gpu'))).toBe(true);
    expect(evaluateCondition(slurmJob, infraFilter('Slurm (prod-gpu)'))).toBe(
      true
    );
  });

  it('ignores case on both sides', () => {
    expect(evaluateCondition(slurmJob, infraFilter('SLURM/PROD-GPU'))).toBe(
      true
    );
  });

  it('does not match another cloud, or a region on the right cloud', () => {
    expect(evaluateCondition(slurmJob, infraFilter('kubernetes'))).toBe(false);
    expect(evaluateCondition(slurmJob, infraFilter('slurm/other'))).toBe(false);
    // A region substring is not a region prefix: `gpu` names no cluster here.
    expect(evaluateCondition(slurmJob, infraFilter('slurm/gpu'))).toBe(false);
  });
});

describe('filterData grouping', () => {
  const rows = [
    { status: 'RUNNING', user: 'alice' },
    { status: 'STOPPED', user: 'alice' },
    { status: 'RUNNING', user: 'bob' },
  ];
  const f = (property, value) => ({ property, operator: ':', value });

  it('ANDs same-property values by default, so existing pages are unchanged', () => {
    const out = filterData(rows, [
      f('Status', 'RUNNING'),
      f('Status', 'STOPPED'),
    ]);
    expect(out).toHaveLength(0);
  });

  it('ORs a property the caller opts in', () => {
    const out = filterData(
      rows,
      [f('Status', 'RUNNING'), f('Status', 'STOPPED')],
      { orProperties: ['Status'] }
    );
    expect(out).toHaveLength(3);
  });

  it('keeps key/value filters intersecting even when opted-in siblings OR', () => {
    const labelled = [
      { status: 'RUNNING', labels: { team: 'ml', env: 'prod' } },
      { status: 'RUNNING', labels: { team: 'ml' } },
    ];
    const out = filterData(
      labelled,
      [f('Labels', 'team:ml'), f('Labels', 'env:prod')],
      { orProperties: ['Status'] }
    );
    expect(out).toHaveLength(1);
  });

  it('ANDs across different properties', () => {
    const out = filterData(rows, [f('Status', 'RUNNING'), f('User', 'alice')]);
    expect(out).toEqual([{ status: 'RUNNING', user: 'alice' }]);
  });

  it('combines both: (status OR status) AND user', () => {
    const out = filterData(
      rows,
      [f('Status', 'RUNNING'), f('Status', 'STOPPED'), f('User', 'alice')],
      { orProperties: ['Status'] }
    );
    expect(out).toHaveLength(2);
  });

  it('returns everything when there are no filters', () => {
    expect(filterData(rows, [])).toBe(rows);
  });
});
