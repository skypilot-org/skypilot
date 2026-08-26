// The volumes table reaches for its data through the shared cache rather than
// taking it as a prop, so the cache is where a fixed set of volumes goes in.
// The table keeps its filters in the URL now, so it reads the router.
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ isReady: true, query: {}, asPath: '/volumes' }),
}));
jest.mock('@/lib/cache', () => ({
  __esModule: true,
  default: { get: jest.fn(), invalidate: jest.fn(), setPreloader: jest.fn() },
}));
jest.mock('@/plugins/PluginSlot', () => ({
  __esModule: true,
  PluginSlot: () => null,
}));
jest.mock('@/plugins/PluginProvider', () => ({
  __esModule: true,
  usePluginComponents: () => [],
  useTableColumns: () => [],
}));

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import dashboardCache from '@/lib/cache';
import { VolumesTable } from '@/components/volumes';

// A CSI provisioner's refusal, long enough that the cell must truncate it.
const CSI_ERROR =
  'PVC is pending. ProvisioningFailed: error generating accessibility ' +
  'requirements: failed to get selected CSINode node-1: ' +
  'csinode.storage.k8s.io "node-1" not found';

const volume = (name, errorMessage = null) => ({
  name,
  status: errorMessage ? 'NOT_READY' : 'READY',
  error_message: errorMessage,
  type: 'k8s-pvc',
  infra: 'Kubernetes/ctx',
  size: 1,
  user_name: 'alice',
});

const renderTable = async (volumes, { refreshInterval = 100000 } = {}) => {
  dashboardCache.get.mockResolvedValue(volumes);
  const result = render(
    <VolumesTable
      refreshInterval={refreshInterval}
      setLoading={() => {}}
      refreshDataRef={{ current: null }}
      onDeleteVolume={() => {}}
      preloadingComplete={true}
    />
  );
  await waitFor(() => expect(screen.getByText(volumes[0].name)).toBeTruthy());
  return result;
};

const headers = (container) =>
  Array.from(container.querySelectorAll('table thead th')).map(
    (th) => th.textContent
  );

describe('VolumesTable details column', () => {
  it('is absent while every volume is usable', async () => {
    const { container } = await renderTable([volume('ok1'), volume('ok2')]);

    expect(headers(container)).not.toContain('Details');
  });

  it('appears once any volume carries a reason, just before the actions', async () => {
    const { container } = await renderTable([
      volume('ok1'),
      volume('broken', CSI_ERROR),
    ]);

    const columns = headers(container);
    expect(columns).toContain('Details');
    expect(columns.indexOf('Details')).toBe(columns.length - 2);
    expect(columns[columns.length - 1]).toBe('Actions');
  });

  // A real pointer press fires mousedown before click, and the outside-click
  // handler listens on mousedown -- so a test that only clicks would miss the
  // toggle collapsing and re-expanding in one press.
  const press = (element) => {
    fireEvent.mouseDown(element);
    fireEvent.click(element);
  };

  it('truncates the reason and reveals the whole of it on demand', async () => {
    await renderTable([volume('broken', CSI_ERROR)]);

    // The point of the column: the text the tooltip used to hide is readable.
    expect(screen.queryByText(CSI_ERROR)).toBeNull();
    press(screen.getByText('... show more'));
    expect(screen.getByText(CSI_ERROR)).toBeTruthy();

    press(screen.getByText('... show less'));
    expect(screen.queryByText(CSI_ERROR)).toBeNull();
  });

  it('collapses on a click anywhere else', async () => {
    const { container } = await renderTable([volume('broken', CSI_ERROR)]);
    press(screen.getByText('... show more'));

    fireEvent.mouseDown(container.querySelector('table'));

    expect(screen.queryByText(CSI_ERROR)).toBeNull();
  });

  it('expands one volume at a time', async () => {
    const other = `${CSI_ERROR} on another volume`;
    await renderTable([volume('broken1', CSI_ERROR), volume('broken2', other)]);

    const [first, second] = screen.getAllByText('... show more');
    press(first);
    press(second);

    expect(screen.getByText(other)).toBeTruthy();
    expect(screen.queryByText(CSI_ERROR)).toBeNull();
  });

  it('leaves a short reason whole, with nothing to expand', async () => {
    await renderTable([volume('broken', 'Storage class not found')]);

    expect(screen.getByText('Storage class not found')).toBeTruthy();
    expect(screen.queryByText('... show more')).toBeNull();
  });

  it('keeps the reason expanded across a refresh', async () => {
    // The table refreshes every few seconds; collapsing under the user
    // mid-read would make a long reason unreadable.
    await renderTable([volume('broken', CSI_ERROR)], { refreshInterval: 20 });
    fireEvent.click(screen.getByText('... show more'));

    await waitFor(() =>
      expect(dashboardCache.get.mock.calls.length).toBeGreaterThan(1)
    );
    expect(screen.getByText(CSI_ERROR)).toBeTruthy();
  });

  it('drops the expanded reason once the volume becomes usable', async () => {
    const { container } = await renderTable([volume('broken', CSI_ERROR)], {
      refreshInterval: 20,
    });
    fireEvent.click(screen.getByText('... show more'));
    dashboardCache.get.mockResolvedValue([volume('broken')]);

    // The column goes with the last reason, so the panel must not outlive it.
    await waitFor(() => expect(headers(container)).not.toContain('Details'));
    expect(screen.queryByText('Full Details')).toBeNull();
  });

  it('does not carry an expanded reason onto another page', async () => {
    const volumes = Array.from({ length: 11 }, (_, i) =>
      volume(`vol${i}`, i === 0 ? CSI_ERROR : null)
    );
    await renderTable(volumes);
    fireEvent.click(screen.getByText('... show more'));

    fireEvent.click(screen.getByText('1 – 10 of 11').nextSibling.children[1]);

    expect(screen.getByText('vol10')).toBeTruthy();
    expect(screen.queryByText('Full Details')).toBeNull();
  });

  it('shows nothing for the usable volumes alongside a broken one', async () => {
    const { container } = await renderTable([
      volume('ok1'),
      volume('broken', CSI_ERROR),
    ]);

    const detailsIndex = headers(container).indexOf('Details');
    const cellsInColumn = Array.from(
      container.querySelectorAll('table tbody tr')
    ).map((row) => row.querySelectorAll('td')[detailsIndex]?.textContent);
    expect(cellsInColumn).toContain('-');
  });
});
