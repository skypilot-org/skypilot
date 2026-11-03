// Table columns: a plugin column's header is its label unless it supplies a
// `header.render` function, which then draws the header cell's content.
jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ isReady: true, query: {}, push: jest.fn() }),
}));
jest.mock('@/data/connectors/client', () => ({
  __esModule: true,
  apiClient: {
    get: jest.fn(async () => ({
      ok: true,
      json: async () => ({ plugins: [] }),
    })),
  },
}));

import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { PluginProvider, useTableColumns } from '@/plugins/PluginProvider';

// Draws each registered header the way the jobs table does.
function Headers() {
  const columns = useTableColumns('jobs');
  return (
    <div>
      {columns.map((col) => (
        <span key={col.id} data-testid={`header-${col.id}`}>
          {col.header.render ? col.header.render() : col.header.label}
        </span>
      ))}
    </div>
  );
}

function register(config) {
  return window.SkyDashboardPluginAPI.registerTableColumn({
    table: 'jobs',
    cell: { render: () => null },
    ...config,
  });
}

test('a header render function replaces the label', async () => {
  render(
    <PluginProvider>
      <Headers />
    </PluginProvider>
  );
  await act(async () => {
    register({
      id: 'custom',
      header: {
        label: 'Select',
        render: () => <input type="checkbox" aria-label="select all" />,
      },
    });
    register({ id: 'plain', header: { label: 'Plain' } });
  });
  expect(screen.getByLabelText('select all')).toBeTruthy();
  expect(screen.getByTestId('header-custom').textContent).toBe('');
  expect(screen.getByTestId('header-plain').textContent).toBe('Plain');
});

test('a header render that is not a function is ignored', async () => {
  render(
    <PluginProvider>
      <Headers />
    </PluginProvider>
  );
  await act(async () => {
    register({ id: 'bad', header: { label: 'Label', render: 'nope' } });
  });
  expect(screen.getByTestId('header-bad').textContent).toBe('Label');
});
