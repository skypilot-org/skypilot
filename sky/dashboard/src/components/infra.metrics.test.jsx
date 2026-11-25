import { act, fireEvent, render, screen } from '@testing-library/react';
import { ContextDetails, SSHNodePoolDetails } from '@/components/infra';
import { checkGrafanaAvailability } from '@/utils/grafana';

jest.mock('@/plugins/PluginSlot', () => ({ PluginSlot: () => null }));
jest.mock('@/components/AllowedNodesHint', () => ({
  AllowedNodesHint: () => null,
  AllowedNodesRowBadge: () => null,
}));
jest.mock('@/utils/grafana', () => ({
  checkGrafanaAvailability: jest.fn(),
  getGrafanaUrl: () => 'https://grafana.example.com',
}));
jest.mock('@/data/connectors/dashboard_config', () => ({
  getDashboardConfig: jest.fn().mockResolvedValue({
    localContexts: ['in-cluster'],
  }),
}));
jest.mock('@/data/connectors/ssh-node-pools', () => ({
  getSSHNodePoolStatus: jest.fn().mockResolvedValue({ status: 'Ready' }),
}));

const gpus = [{ gpu_name: 'H100', gpu_total: 1, gpu_free: 1 }];
const poolProps = (poolName) => ({
  poolName,
  gpusInContext: gpus,
  nodesInContext: [{ node_name: `node-${poolName}`, gpu_count: 1 }],
});
const panelParams = () =>
  new URL(screen.getByTitle('GPU Utilization').src).searchParams;

beforeEach(() => {
  checkGrafanaAvailability.mockResolvedValue(true);
  fetch.mockImplementation(async (url) => {
    const query = new URL(url).searchParams.get('query');
    const node = query.includes('ssh-east') ? 'node-east' : 'node-west';
    return {
      ok: true,
      json: async () => ({ data: { result: [{ metric: { node } }] } }),
    };
  });
});

test('SSH panels preserve the pool prefix and reset the node filter on pool change', async () => {
  const { rerender, container } = render(
    <SSHNodePoolDetails {...poolProps('east')} />
  );
  await screen.findByRole('option', { name: 'node-east' });
  expect(container.querySelectorAll('iframe')).toHaveLength(6);
  expect(panelParams().get('var-cluster')).toBe('ssh-east');

  fireEvent.change(screen.getByLabelText('Node:'), {
    target: { value: 'node-east' },
  });
  expect(panelParams().get('var-host')).toBe('node-east');
  fireEvent.click(screen.getByRole('button', { name: '6h' }));
  expect(panelParams().get('from')).toBe('now-6h');

  rerender(<SSHNodePoolDetails {...poolProps('west')} />);
  await screen.findByRole('option', { name: 'node-west' });
  expect(screen.getByLabelText('Node:')).toHaveValue('$__all');
  expect(panelParams().get('var-cluster')).toBe('ssh-west');
  expect(panelParams().get('var-host')).toBe('$__all');
});

test('SSH pool refresh remounts all GPU panels', async () => {
  const props = poolProps('east');
  const { rerender, container } = render(
    <SSHNodePoolDetails {...props} gpuMetricsRefreshTrigger={0} />
  );
  await screen.findByTitle('GPU Utilization');
  const panels = Array.from(container.querySelectorAll('iframe'));
  rerender(<SSHNodePoolDetails {...props} gpuMetricsRefreshTrigger={1} />);
  const refreshed = Array.from(container.querySelectorAll('iframe'));
  expect(refreshed).toHaveLength(6);
  panels.forEach((panel, index) => {
    expect(refreshed[index]).not.toBe(panel);
    expect(refreshed[index].src).toBe(panel.src);
  });
});

test.each([
  ['Kubernetes', 'k8s-east', gpus, false, true, true],
  ['Slurm', 'slurm-east', gpus, true, true, false],
  ['GPU-free pool', 'ssh-east', [], false, true, false],
  ['unavailable Grafana', 'ssh-east', gpus, false, false, false],
])(
  '%s retains GPU panel visibility rules',
  async (_name, contextName, gpuData, isSlurm, available, visible) => {
    checkGrafanaAvailability.mockResolvedValue(available);
    await act(async () => {
      render(
        <ContextDetails
          contextName={contextName}
          gpusInContext={gpuData}
          nodesInContext={[]}
          isSlurm={isSlurm}
        />
      );
    });
    expect(Boolean(screen.queryByTitle('GPU Utilization'))).toBe(visible);
    if (visible) expect(panelParams().get('var-cluster')).toBe(contextName);
  }
);
