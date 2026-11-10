import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { Status2Actions } from './clusters';

// useMobile reads window.matchMedia (absent in jsdom) and, when true, hides the
// action labels. Pin it to desktop so the "Connect"/"VSCode" labels render.
jest.mock('@/hooks/useMobile', () => ({ useMobile: () => false }));
// Analytics is fire-and-forget; stub it so a click doesn't reach the real
// telemetry path.
jest.mock('@/lib/analytics', () => ({
  trackClusterAction: jest.fn(),
  trackFilterUsed: jest.fn(),
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
    expect(buttons).toHaveLength(2);

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
