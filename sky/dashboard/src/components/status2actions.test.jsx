import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { Status2Actions } from './clusters';
import { terminateCluster } from '@/data/connectors/clusters';

// useMobile reads window.matchMedia (absent in jsdom) and, when true, hides the
// action labels. Pin it to desktop so the "Connect"/"VSCode" labels render.
jest.mock('@/hooks/useMobile', () => ({ useMobile: () => false }));
// Analytics is fire-and-forget; stub it so a click doesn't reach the real
// telemetry path.
jest.mock('@/lib/analytics', () => ({
  trackClusterAction: jest.fn(),
  trackFilterUsed: jest.fn(),
}));
jest.mock('@/data/connectors/clusters', () => ({
  terminateCluster: jest.fn(),
}));

// Status2Actions gates the Connect (SSH) and VSCode actions on `writable`:
// both open an interactive shell (a write), so a cluster in a workspace the
// user can only read must render them disabled and inert. This is the visible
// half of the read-only-visibility feature; without the gate, a non-member
// could click straight into a read-only cluster from the dashboard.
describe('Status2Actions writable gating', () => {
  const renderActions = (writable) => {
    const onOpenSSHModal = jest.fn();
    const onOpenVSCodeModal = jest.fn();
    render(
      <Status2Actions
        withLabel
        cluster="my-cluster"
        status="RUNNING"
        onOpenSSHModal={onOpenSSHModal}
        onOpenVSCodeModal={onOpenVSCodeModal}
        writable={writable}
      />
    );
    return { onOpenSSHModal, onOpenVSCodeModal };
  };

  it('renders Connect/VSCode as clickable and fires the modals when writable', () => {
    const { onOpenSSHModal, onOpenVSCodeModal } = renderActions(true);

    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(3);

    fireEvent.click(screen.getByText('Connect'));
    expect(onOpenSSHModal).toHaveBeenCalledWith('my-cluster');

    fireEvent.click(screen.getByText('VSCode'));
    expect(onOpenVSCodeModal).toHaveBeenCalledWith('my-cluster');
  });

  it('disables Connect/VSCode and does not fire the modals when not writable', () => {
    const { onOpenSSHModal, onOpenVSCodeModal } = renderActions(false);

    // No clickable buttons: both actions render as disabled spans instead.
    expect(screen.queryByRole('button')).toBeNull();
    // The actions are still shown (visible, just read-only), not hidden.
    expect(screen.getByText('Connect')).toBeInTheDocument();
    expect(screen.getByText('VSCode')).toBeInTheDocument();

    // Clicking the disabled action must not open a shell.
    fireEvent.click(screen.getByText('Connect'));
    fireEvent.click(screen.getByText('VSCode'));
    expect(onOpenSSHModal).not.toHaveBeenCalled();
    expect(onOpenVSCodeModal).not.toHaveBeenCalled();
  });
});

describe('Status2Actions terminate', () => {
  const renderTerminate = (props = {}) =>
    render(
      <Status2Actions
        withLabel
        cluster="my-cluster"
        status="RUNNING"
        workspace="team-a"
        {...props}
      />
    );

  it.each(['RUNNING', 'STOPPED', 'LAUNCHING', 'UNHEALTHY'])(
    'offers Terminate for %s clusters',
    (status) => {
      renderTerminate({ status });
      expect(screen.getByText('Terminate').closest('button')).not.toBeNull();
    }
  );

  it.each(['PENDING', 'TERMINATED'])(
    'renders Terminate disabled for %s clusters',
    (status) => {
      renderTerminate({ status });
      // Like the other actions, an unavailable one stays visible but inert.
      expect(screen.queryByRole('button')).toBeNull();
      fireEvent.click(screen.getByText('Terminate'));
      expect(screen.queryByText('Terminate cluster')).toBeNull();
    }
  );

  it('hides Terminate for managed and controller clusters', () => {
    const { unmount } = renderTerminate({ managed: true });
    expect(screen.queryByText('Terminate')).toBeNull();
    unmount();
    renderTerminate({ cluster: 'sky-jobs-controller-1234abcd' });
    expect(screen.queryByText('Terminate')).toBeNull();
  });

  it('disables Terminate when not writable', () => {
    renderTerminate({ writable: false });
    expect(screen.queryByRole('button')).toBeNull();
    fireEvent.click(screen.getByText('Terminate'));
    expect(screen.queryByText('Terminate cluster')).toBeNull();
    expect(terminateCluster).not.toHaveBeenCalled();
  });

  it('confirms, then terminates in the cluster workspace and refreshes', async () => {
    terminateCluster.mockResolvedValue(true);
    const onTerminated = jest.fn();
    renderTerminate({ onTerminated });

    fireEvent.click(screen.getByText('Terminate'));
    expect(
      screen.getByText(
        'Terminate my-cluster? This deletes the cluster and its local disk. Running jobs are killed.'
      )
    ).toBeInTheDocument();
    expect(terminateCluster).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Terminate' }));
    await waitFor(() => expect(onTerminated).toHaveBeenCalled());
    expect(terminateCluster).toHaveBeenCalledWith({
      clusterName: 'my-cluster',
      workspace: 'team-a',
    });
  });

  it('does not report success when the terminate request fails', async () => {
    terminateCluster.mockResolvedValue(false);
    const onTerminated = jest.fn();
    renderTerminate({ onTerminated });

    fireEvent.click(screen.getByText('Terminate'));
    fireEvent.click(screen.getByRole('button', { name: 'Terminate' }));
    await waitFor(() => expect(terminateCluster).toHaveBeenCalled());
    expect(onTerminated).not.toHaveBeenCalled();
  });
});
