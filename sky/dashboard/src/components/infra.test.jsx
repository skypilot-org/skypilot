// Expose each plugin slot's name and row context as data attributes so the
// row-identity contract can be asserted. Renders no text, so the cell-content
// assertions below are unaffected.
jest.mock('@/plugins/PluginSlot', () => ({
  __esModule: true,
  PluginSlot: ({ name, context }) => (
    <span
      data-slot={name}
      data-row-id={context?.id}
      data-row-kind={context?.kind}
    />
  ),
}));

import { fireEvent, render, screen } from '@testing-library/react';
import {
  InfrastructureSection,
  slurmRequestableCounts,
} from '@/components/infra';

// The infrastructure table renders one sub-row per GPU type. A Slurm cluster
// can additionally be expanded into its partitions, which is where the
// overlap lives: a node in several partitions is counted in each, so the
// partition rows do not add up to the collapsed cluster totals.
const normalize = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const renderSection = (props) =>
  render(
    <InfrastructureSection
      title="Slurm"
      isLoading={false}
      isDataLoaded={true}
      handleContextClick={() => {}}
      isInitialLoad={false}
      groupedPerContextGPUs={{}}
      groupedPerNodeGPUs={{}}
      {...props}
    />
  );

// Desktop table and mobile cards render the same data, so scope queries to the
// table.
const tableRows = (container) =>
  Array.from(container.querySelectorAll('table tbody tr'));

const cellTexts = (row) =>
  Array.from(row.querySelectorAll('td')).map(normalize);

const expandPartitions = () =>
  fireEvent.click(screen.getAllByTitle('Show partitions')[0]);

describe('InfrastructureSection Slurm partition rows', () => {
  const slurmNode = (nodeName, partition, gpuName, total, free) => ({
    node_name: nodeName,
    partition,
    gpu_name: gpuName,
    gpu_total: total,
    gpu_free: free,
    cluster: 'nebius-slinky',
  });

  // 'real*' is the default partition (sinfo marks it with a trailing '*');
  // n3/n4 sit in both fabric-a and mock, and mock also holds an A100 node so
  // one partition spans two GPU types.
  const nodes = [
    slurmNode('n1', 'real*', 'H100', 8, 0),
    slurmNode('n2', 'real*', 'H100', 8, 4),
    slurmNode('n3', 'fabric-a,mock', 'H100', 8, 8),
    slurmNode('n4', 'fabric-a,mock', 'H100', 8, 0),
    slurmNode('n5', 'mock', 'A100', 4, 4),
  ];

  const renderSlurm = () =>
    renderSection({
      isSlurm: true,
      contexts: ['nebius-slinky'],
      gpus: [{ gpu_name: 'H100', gpu_total: 32, gpu_free: 12 }],
      groupedPerContextGPUs: {
        'nebius-slinky': [
          {
            gpu_name: 'H100',
            gpu_total: 32,
            gpu_free: 12,
            cluster: 'nebius-slinky',
          },
        ],
      },
      groupedPerNodeGPUs: { 'nebius-slinky': nodes },
    });

  it('collapses to the cluster totals, counting what expanding reveals', () => {
    const { container } = renderSlurm();
    const rows = tableRows(container);
    // One row for the cluster's single GPU type, not one per partition.
    expect(rows).toHaveLength(1);
    expect(cellTexts(rows[0])).toContain('3 partitions');
    expect(cellTexts(rows[0])).toEqual(
      expect.arrayContaining(['H100', '12 of 32 free'])
    );
    expect(normalize(rows[0])).toMatch(/nebius-slinky\s*5/);
    expect(container.querySelector('table').textContent).not.toContain(
      'fabric-a'
    );
  });

  it('appends the partition rows under the cluster totals when expanded', () => {
    const { container } = renderSlurm();
    expandPartitions();
    const rows = tableRows(container);
    // The cluster's own row stays on top, keeping the aggregate on screen.
    expect(rows).toHaveLength(5);
    expect(cellTexts(rows[0])).toEqual(
      expect.arrayContaining(['3 partitions', 'H100', '12 of 32 free'])
    );
    expect(cellTexts(rows[1])).toContain('fabric-a');
    expect(cellTexts(rows[2])).toContain('mock');
    // The cluster's node count stays deduplicated: 5 nodes, not one per
    // partition membership.
    expect(normalize(rows[0])).toMatch(/nebius-slinky\s*5/);
  });

  it('counts a single-partition cluster the same way', () => {
    renderSection({
      isSlurm: true,
      contexts: ['crusoe-slurm-use1'],
      gpus: [{ gpu_name: 'A100', gpu_total: 8, gpu_free: 8 }],
      groupedPerContextGPUs: {
        'crusoe-slurm-use1': [{ gpu_name: 'A100', gpu_total: 8, gpu_free: 8 }],
      },
      groupedPerNodeGPUs: {
        'crusoe-slurm-use1': [
          {
            node_name: 'c1',
            partition: 'all*',
            gpu_name: 'A100',
            gpu_total: 8,
            gpu_free: 8,
          },
        ],
      },
    });
    expect(screen.getAllByText(/1 partition$/)[0]).toBeTruthy();
    expandPartitions();
    expect(screen.getAllByText('all')[0]).toBeTruthy();
  });

  it('sums GPU capacity per partition from its member nodes', () => {
    const { container } = renderSlurm();
    expandPartitions();
    const rows = tableRows(container);
    // fabric-a and mock both count n3 + n4's 16 H100s: shared capacity is
    // reachable from either partition, so it is not split between them.
    expect(cellTexts(rows[1])).toEqual(
      expect.arrayContaining(['H100', '8 of 16 free'])
    );
    expect(cellTexts(rows[2])).toEqual(
      expect.arrayContaining(['H100', '8 of 16 free'])
    );
    expect(cellTexts(rows[3])).toEqual(
      expect.arrayContaining(['A100', '4 of 4 free'])
    );
    expect(cellTexts(rows[4])).toEqual(
      expect.arrayContaining(['H100', '4 of 16 free'])
    );
  });

  it('marks the default partition and spans multi-GPU-type partitions', () => {
    const { container } = renderSlurm();
    expandPartitions();
    const rows = tableRows(container);
    // The gap before the tag is a margin, not whitespace in the text.
    expect(cellTexts(rows[4])).toContain('real(default)');
    // 'mock' has H100 and A100 rows, so its cell spans both; the second row
    // carries only the GPU cells.
    const mockCell = Array.from(rows[2].querySelectorAll('td')).find((td) =>
      td.textContent.includes('mock')
    );
    expect(mockCell.getAttribute('rowspan')).toBe('2');
    expect(rows[3].querySelectorAll('td')).toHaveLength(3);
  });

  it('gives the cluster cells a rowSpan covering totals and partitions', () => {
    const { container } = renderSlurm();
    expandPartitions();
    const nameCell = Array.from(
      container.querySelectorAll('table tbody td')
    ).find((td) => td.textContent.includes('nebius-slinky'));
    expect(nameCell.getAttribute('rowspan')).toBe('5');
    // The partition count spans only the cluster's own rows.
    const summaryCell = Array.from(
      container.querySelectorAll('table tbody td')
    ).find((td) => td.textContent.includes('3 partitions'));
    expect(summaryCell.getAttribute('rowspan')).toBe('1');
  });
});

// A cluster configured in ~/.slurm/config is listed whether or not it answers
// a query, so an unreachable login node leaves the section with a cluster name
// and nothing else to show for it.
describe('InfrastructureSection unreachable Slurm cluster', () => {
  it('renders one row with empty node, partition and GPU cells', () => {
    const { container } = renderSection({
      isSlurm: true,
      contexts: ['offline-cluster'],
      gpus: [],
      groupedPerContextGPUs: {},
      groupedPerNodeGPUs: {},
    });
    const rows = tableRows(container);
    expect(rows).toHaveLength(1);
    expect(normalize(rows[0])).toMatch(/offline-cluster\s*0/);
    // Partition cell plus the three GPU cells, all with nothing to report.
    expect(cellTexts(rows[0]).filter((text) => text === '-')).toHaveLength(4);
    expect(container.textContent).toContain('1 cluster');
    expect(container.textContent).not.toContain('not configured');
  });

  // The row's identity is what a plugin keys its status off, so it has to be
  // the same for an unreachable cluster as for a healthy one.
  it('keeps the namePrefix slot on the row, keyed slurm/<cluster name>', () => {
    const { container } = renderSection({
      isSlurm: true,
      contexts: ['offline-cluster'],
      gpus: [],
      groupedPerContextGPUs: {},
      groupedPerNodeGPUs: {},
    });
    const slot = container.querySelector(
      'table tbody [data-slot="infra.row.namePrefix"]'
    );
    expect(slot).not.toBeNull();
    expect(slot.getAttribute('data-row-kind')).toBe('slurm');
    expect(slot.getAttribute('data-row-id')).toBe('offline-cluster');
  });
});

// A failed section-wide query (e.g. the SSH error of an unreachable Slurm
// login node) arrives as `sectionError` and renders as a banner, so the page
// says what went wrong instead of silently showing empty cells.
describe('InfrastructureSection section-wide error banner', () => {
  const sshError =
    'ssh: connect to host 10.0.0.5 port 22: Connection timed out';

  it('shows the failure text above the listed cluster rows', () => {
    const { container } = renderSection({
      isSlurm: true,
      contexts: ['offline-cluster'],
      gpus: [],
      sectionError: sshError,
    });
    expect(container.textContent).toContain('Failed to query Slurm');
    expect(container.textContent).toContain(sshError);
    // The configured cluster still renders as a row under the banner.
    expect(tableRows(container)).toHaveLength(1);
    expect(normalize(tableRows(container)[0])).toContain('offline-cluster');
  });

  it('shows the banner instead of the "not configured" guess when empty', () => {
    const { container } = renderSection({
      isSlurm: true,
      contexts: [],
      gpus: [],
      sectionError: sshError,
    });
    expect(container.textContent).toContain('Failed to query Slurm');
    expect(container.textContent).toContain(sshError);
    // The banner already explains why nothing is listed; guessing "not
    // configured" next to it would contradict it.
    expect(container.textContent).not.toContain('not configured');
  });

  it('renders no banner when there is no error', () => {
    const { container } = renderSection({
      isSlurm: true,
      contexts: ['healthy-cluster'],
      gpus: [],
      sectionError: null,
    });
    expect(container.textContent).not.toContain('Failed to query');
  });
});

// Mirrors slurm_catalog.list_accelerators_realtime, which feeds the
// "Requestable: N / node" tooltip. If the backend rule changes, this is the
// copy that has to move with it.
describe('slurmRequestableCounts', () => {
  it('returns powers of two up to a power-of-two node size', () => {
    expect(slurmRequestableCounts(8)).toEqual([1, 2, 4, 8]);
  });

  it('appends the node size when it is not a power of two', () => {
    expect(slurmRequestableCounts(6)).toEqual([1, 2, 4, 6]);
  });

  it('returns nothing for a CPU-only node', () => {
    expect(slurmRequestableCounts(0)).toEqual([]);
  });
});

describe('InfrastructureSection Kubernetes GPU-type rows', () => {
  it('still renders one row per GPU type with no partition column', () => {
    const { container } = renderSection({
      title: 'Kubernetes',
      contexts: ['usw9b'],
      gpus: [
        { gpu_name: 'H100', gpu_total: 256, gpu_free: 67 },
        { gpu_name: 'B200', gpu_total: 256, gpu_free: 88 },
      ],
      groupedPerContextGPUs: {
        usw9b: [
          { gpu_name: 'H100', gpu_total: 256, gpu_free: 67, context: 'usw9b' },
          { gpu_name: 'B200', gpu_total: 256, gpu_free: 88, context: 'usw9b' },
        ],
      },
      groupedPerNodeGPUs: { usw9b: [] },
      loadedContexts: new Set(['usw9b']),
    });
    const rows = tableRows(container);
    expect(rows).toHaveLength(2);
    expect(cellTexts(rows[0])).toEqual(
      expect.arrayContaining(['H100', '67 of 256 free'])
    );
    expect(cellTexts(rows[1])).toEqual(
      expect.arrayContaining(['B200', '88 of 256 free'])
    );
    expect(container.querySelector('table').textContent).not.toContain(
      'Partition'
    );
  });
});
