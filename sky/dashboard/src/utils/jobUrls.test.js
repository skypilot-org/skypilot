import { dynamicTaskUrl } from './jobUrls';

describe('dynamicTaskUrl', () => {
  it('points at the task under its job group', () => {
    expect(dynamicTaskUrl(66, 2, { job: '67' })).toBe('/jobs/66/2');
  });

  it('keeps tab', () => {
    expect(dynamicTaskUrl(66, 2, { job: '67', tab: 'logs' })).toBe(
      '/jobs/66/2?tab=logs'
    );
  });

  it('keeps the bare ?node_rdma preview flag', () => {
    // Next.js parses a bare ?node_rdma as node_rdma: ''.
    expect(dynamicTaskUrl(66, 2, { job: '67', node_rdma: '' })).toBe(
      '/jobs/66/2?node_rdma='
    );
  });

  it('keeps tab and ?node_rdma together', () => {
    expect(
      dynamicTaskUrl(66, 2, { job: '67', tab: 'logs', node_rdma: '1' })
    ).toBe('/jobs/66/2?tab=logs&node_rdma=1');
  });

  it('keeps every value of a repeated parameter', () => {
    expect(dynamicTaskUrl(66, 2, { job: '67', a: ['1', '2'] })).toBe(
      '/jobs/66/2?a=1&a=2'
    );
  });
});
