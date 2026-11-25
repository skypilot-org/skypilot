// The Telemetry section embeds one Grafana panel per entry. The node RDMA
// panels are in preview: they show only with ?node_rdma in the page URL, and,
// like the GPU panels, only for clusters with GPUs.
let mockQuery = {};
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ isReady: true, query: mockQuery }),
}));

import React from 'react';
import { render, screen } from '@testing-library/react';
import { TelemetrySection } from './TelemetrySection';

const STORAGE_KEY = 'telemetry-test-expanded';
const RDMA = 'Node RDMA Network Throughput';
const RDMA_DROPS = 'RDMA Link Drops';

function panelTitles({ query, hasGpu = true }) {
  mockQuery = query;
  localStorage.setItem(STORAGE_KEY, 'true');
  const { unmount } = render(
    <TelemetrySection
      clusterNameOnCloud="train-2ea4"
      displayName="train"
      storageKey={STORAGE_KEY}
      hasGpu={hasGpu}
    />
  );
  const titles = screen
    .getAllByTitle(/./)
    .filter((el) => el.tagName === 'IFRAME')
    .map((el) => el.title);
  unmount();
  return titles;
}

describe('TelemetrySection node RDMA preview', () => {
  test('hides the node RDMA panels without ?node_rdma', () => {
    const titles = panelTitles({ query: {} });
    expect(titles).toContain('GPU Utilization');
    expect(titles).not.toContain(RDMA);
    expect(titles).not.toContain(RDMA_DROPS);
  });

  test('shows the node RDMA panels with ?node_rdma', () => {
    for (const flag of ['', '1']) {
      const titles = panelTitles({ query: { node_rdma: flag } });
      expect(titles).toContain(RDMA);
      expect(titles).toContain(RDMA_DROPS);
    }
  });

  test('keeps them hidden for a cluster without GPUs', () => {
    const titles = panelTitles({ query: { node_rdma: '' }, hasGpu: false });
    expect(titles).toEqual(['CPU Usage (cores)', 'Memory Usage']);
  });

  test.each([
    [RDMA, '8'],
    [RDMA_DROPS, '10'],
  ])('points %s at panel %s of the GPU dashboard', (title, panelId) => {
    mockQuery = { node_rdma: '' };
    localStorage.setItem(STORAGE_KEY, 'true');
    render(
      <TelemetrySection
        clusterNameOnCloud="train-2ea4"
        displayName="train"
        storageKey={STORAGE_KEY}
      />
    );
    const src = screen.getByTitle(title).getAttribute('src');
    expect(src).toContain(
      '/d-solo/skypilot-dcgm-gpu/skypilot-dcgm-gpu-metrics?'
    );
    expect(new URL(src).searchParams.get('panelId')).toBe(panelId);
    expect(new URL(src).searchParams.get('var-cluster')).toBe('train-2ea4');
  });
});
