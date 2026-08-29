import { getJobGpuCount, USER_FILTER_SCHEMA } from '@/components/users';
import {
  filtersToQuery,
  legacyFiltersToQuery,
  queryToFilters,
} from '@/components/shared/filterSchema';

// getJobGpuCount decides how many GPUs a managed job contributes to a user's
// GPU total on the Users page. The critical regression it guards against:
// jobs that are STARTING/PENDING (cluster still provisioning, e.g. a k8s pod
// sitting Pending) must NOT be counted, otherwise per-user GPU totals can
// exceed the physical cluster capacity (SKY-5730).
describe('getJobGpuCount', () => {
  const makeJob = (overrides) => ({
    status: 'RUNNING',
    accelerators: { H100: 8 },
    resources_str_full: '1x(H100:8)',
    job_id: 1,
    ...overrides,
  });

  it('counts GPUs for a single-node RUNNING job', () => {
    expect(getJobGpuCount(makeJob({ status: 'RUNNING' }))).toBe(8);
  });

  it('multiplies per-node GPUs by the number of nodes', () => {
    expect(
      getJobGpuCount(
        makeJob({ status: 'RUNNING', resources_str_full: '4x(H100:8)' })
      )
    ).toBe(32);
  });

  // Regression: these transient states reported their requested accelerators
  // before the GPUs were actually allocated, inflating the total above
  // cluster capacity.
  it.each(['STARTING', 'PENDING', 'SUBMITTED'])(
    'does not count GPUs for a %s job',
    (status) => {
      expect(getJobGpuCount(makeJob({ status }))).toBe(0);
    }
  );

  // A RECOVERING job is re-acquiring the same resources, and a CANCELLING job
  // still holds GPUs until its cluster is torn down, so both are counted.
  it.each(['RECOVERING', 'CANCELLING'])(
    'counts GPUs for a %s job',
    (status) => {
      expect(
        getJobGpuCount(makeJob({ status, accelerators: { A100: 4 } }))
      ).toBe(4);
    }
  );

  it('returns 0 for a RUNNING job with no accelerators (CPU-only)', () => {
    expect(
      getJobGpuCount(
        makeJob({
          status: 'RUNNING',
          accelerators: null,
          resources_str_full: '1x(vcpu=4)',
        })
      )
    ).toBe(0);
  });

  it('parses accelerators provided as a string', () => {
    expect(
      getJobGpuCount(
        makeJob({ status: 'RUNNING', accelerators: "{'V100': 4}" })
      )
    ).toBe(4);
  });

  it('defaults to a single node when resources_str_full is missing', () => {
    expect(
      getJobGpuCount(
        makeJob({ status: 'RUNNING', resources_str_full: undefined })
      )
    ).toBe(8);
  });

  it('returns 0 for a null or undefined job', () => {
    expect(getJobGpuCount(null)).toBe(0);
    expect(getJobGpuCount(undefined)).toBe(0);
  });
});

// The Users page GPU and Infra filters are multi-valued: the table's counting
// path reads several values on either as alternatives. That has to survive the
// trip through the URL, or a link asking for two GPUs comes back showing one.
describe('USER_FILTER_SCHEMA', () => {
  const chip = (property, value) => ({ property, operator: ':', value });

  it('joins several GPU values into one readable parameter', () => {
    expect(
      filtersToQuery(USER_FILTER_SCHEMA, [
        chip('GPU', 'A100'),
        chip('GPU', 'A10G'),
      ])
    ).toEqual({ gpu: 'A100,A10G' });
  });

  it('reads a GPU list back as one chip per value', () => {
    expect(queryToFilters(USER_FILTER_SCHEMA, { gpu: 'A100,A10G' })).toEqual([
      chip('GPU', 'A100'),
      chip('GPU', 'A10G'),
    ]);
  });

  it('keeps both values of a legacy two-triple GPU link', () => {
    // What the old dropdown wrote for "either of these GPUs". Collapsing it to
    // one value silently narrows an already-shared link.
    expect(
      legacyFiltersToQuery(USER_FILTER_SCHEMA, {
        property: ['gpu type', 'gpu type'],
        operator: [':', ':'],
        value: ['A100', 'A10G'],
      })
    ).toEqual({ gpu: 'A100,A10G' });
  });

  it('treats infra the same way', () => {
    expect(
      filtersToQuery(USER_FILTER_SCHEMA, [
        chip('Infra', 'AWS'),
        chip('Infra', 'GCP'),
      ])
    ).toEqual({ infra: 'AWS,GCP' });
  });

  it('keeps the single-valued properties single', () => {
    // A user has one name, one id, one role; a second value could only ever
    // empty the table, so the last one wins.
    expect(
      filtersToQuery(USER_FILTER_SCHEMA, [
        chip('Name', 'alice'),
        chip('Name', 'bob'),
      ])
    ).toEqual({ name: 'bob' });
  });
});
