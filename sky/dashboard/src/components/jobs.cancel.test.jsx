jest.mock('next/router', () => ({
  __esModule: true,
  useRouter: () => ({ query: {}, push: jest.fn() }),
}));
jest.mock('@/lib/analytics', () => ({
  trackJobAction: jest.fn(),
  trackFilterUsed: jest.fn(),
}));
jest.mock('@/data/connectors/jobs', () => ({
  handleJobAction: jest.fn(),
  downloadManagedJobLogs: jest.fn(),
}));
jest.mock('@/data/connectors/clusters', () => ({
  getClusters: jest.fn(),
  downloadJobLogs: jest.fn(),
}));

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Status2Actions } from '@/components/jobs';
import { handleJobAction } from '@/data/connectors/jobs';
import { trackJobAction } from '@/lib/analytics';

const cancelProps = (overrides = {}) => ({
  name: 'train',
  user: 'alice',
  currentUser: { id: 'hash-alice', name: 'alice' },
  writable: true,
  onCancelled: jest.fn(),
  ...overrides,
});

const renderActions = (cancel) =>
  render(
    <Status2Actions
      withLabel
      jobParent="/jobs"
      jobId={42}
      managed={true}
      workspace="team-a"
      cancel={cancel}
    />
  );

describe('managed job Status2Actions cancel', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows no Cancel action without a cancel prop (finished job)', () => {
    renderActions(null);
    expect(screen.queryByText('Cancel')).toBeNull();
  });

  it('disables Cancel when the workspace is not writable', () => {
    renderActions(cancelProps({ writable: false }));
    fireEvent.click(screen.getByText('Cancel'));
    expect(screen.queryByText('Cancel managed job')).toBeNull();
    expect(handleJobAction).not.toHaveBeenCalled();
  });

  it('confirms, then cancels in the job workspace and refreshes', async () => {
    handleJobAction.mockResolvedValue(true);
    const cancel = cancelProps();
    renderActions(cancel);

    fireEvent.click(screen.getByText('Cancel'));
    expect(trackJobAction).toHaveBeenCalledWith('cancel', { jobId: 42 });
    expect(
      screen.getByText(
        'Cancel managed job 42 (train)? Its cluster is torn down and the job is not recovered.'
      )
    ).toBeInTheDocument();
    expect(handleJobAction).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Yes, cancel job' }));
    await waitFor(() => expect(cancel.onCancelled).toHaveBeenCalled());
    expect(handleJobAction).toHaveBeenCalledWith('cancel', 42, null, 'team-a');
    // Hidden right away: the queue still reports the job as RUNNING.
    expect(screen.queryByText('Cancel')).toBeNull();
  });

  it('keeps Cancel available when the request fails', async () => {
    handleJobAction.mockResolvedValue(false);
    const cancel = cancelProps();
    renderActions(cancel);

    fireEvent.click(screen.getByText('Cancel'));
    fireEvent.click(screen.getByRole('button', { name: 'Yes, cancel job' }));
    await waitFor(() => expect(handleJobAction).toHaveBeenCalled());
    expect(cancel.onCancelled).not.toHaveBeenCalled();
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it("names the owner when the job is another user's", () => {
    renderActions(cancelProps({ user: 'bob' }));
    fireEvent.click(screen.getByText('Cancel'));
    expect(
      screen.getByText(/This job was launched by bob\.$/)
    ).toBeInTheDocument();
  });
});
