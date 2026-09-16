import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import React from 'react';

import { JobStartupTimeline } from './jobs.startuptimeline';

const GATED_JOB = {
  t_time_to_running: 1000,
  t_controller_queue: 10,
  t_retry_overhead: 0,
  t_provision_setup: 20,
  t_queue_wait: 600,
  t_node_startup: 300,
  t_runtime_setup: 70,
};

// The same job 40 minutes earlier: claimed, instances asked for, and sitting
// in a scheduler queue. No t_* column exists yet.
const QUEUED_JOB = {
  startup_progress: {
    total: 2430,
    phases: {
      controller_queue: 10,
      retry_overhead: 0,
      provision_setup: 20,
      queue_wait: 2400,
    },
    open_phase: 'queue_wait',
  },
};

describe('JobStartupTimeline', () => {
  it('shows where the time went, not just that it was slow', () => {
    render(<JobStartupTimeline jobData={GATED_JOB} />);

    // Naming the phase is the whole point: a long quota wait and a long node
    // startup are the same number but different people's problem.
    expect(screen.getByText('Waiting for quota')).toBeInTheDocument();
    expect(screen.getByText('Starting the nodes')).toBeInTheDocument();
  });

  it('sizes each segment by its share of the wait', () => {
    render(<JobStartupTimeline jobData={GATED_JOB} />);

    // 600 of 1000 seconds were spent queueing, so the bar must say so; a bar
    // that does not scale is decoration rather than a measurement.
    expect(screen.getByTestId('startup-segment-queue_wait')).toHaveStyle(
      'width: 60%'
    );
    expect(screen.getByTestId('startup-segment-node_startup')).toHaveStyle(
      'width: 30%'
    );
  });

  it('omits phases that did not happen', () => {
    render(<JobStartupTimeline jobData={GATED_JOB} />);

    // This job never retried. Drawing a zero-width segment and a legend entry
    // for it would suggest it did.
    expect(screen.queryByText('Retried launches')).not.toBeInTheDocument();
  });

  it('draws a pool job as a full bar, not a 1%-wide one', () => {
    // A pool job never provisions, so its whole wait lands in one phase. Leave
    // that phase out of the bar and the header still reads the full time while
    // the bar renders almost empty -- which reads as a rendering bug rather
    // than as the measurement it is.
    render(
      <JobStartupTimeline
        jobData={{
          t_time_to_running: 1000,
          t_controller_queue: 10,
          t_unattributed: 990,
        }}
      />
    );

    expect(screen.getByTestId('startup-segment-unattributed')).toHaveStyle(
      'width: 99%'
    );
  });

  it('shows both ends of the bar, so nothing has to be subtracted', () => {
    // The bug this panel was parked for: the bar spans eligible_at ->
    // start_at, while the page's "Submitted" is a later moment, so a reader
    // subtracting the two visible timestamps got a third number. Showing both
    // of the bar's own ends closes it over itself.
    render(
      <JobStartupTimeline
        jobData={{
          ...GATED_JOB,
          eligible_at: new Date('2026-09-16T09:23:14Z'),
          started_at: new Date('2026-09-16T09:39:54Z'),
        }}
      />
    );

    expect(screen.getByText(/^from .* to .*/)).toBeInTheDocument();
  });

  it('draws the bar even when the timestamps are missing', () => {
    // Jobs recorded before these were carried have the phases but neither
    // end. The breakdown is still the useful part, so it must not vanish with
    // them -- and formatting a missing date must not throw.
    render(<JobStartupTimeline jobData={GATED_JOB} />);

    expect(screen.getByText('Waiting for quota')).toBeInTheDocument();
    expect(screen.queryByText(/^from /)).not.toBeInTheDocument();
  });

  it('takes the timestamps as Dates, which is what the connector sends', () => {
    // formatFullTimestamp calls getFullYear, so a raw epoch number throws --
    // and only on a job that HAS a breakdown, which is the one case the panel
    // exists for. Passing numbers here is what that mistake looks like.
    expect(() =>
      render(
        <JobStartupTimeline
          jobData={{
            ...GATED_JOB,
            eligible_at: 1789550594,
            started_at: 1789551594,
          }}
        />
      )
    ).toThrow();
  });

  it('draws a job that has not started yet from its live progress', () => {
    // The state someone opens this page in: every t_* is still null because
    // the recorder needs start_at, so without the live source the panel is
    // blank for exactly the job that needed explaining.
    render(
      <JobStartupTimeline jobData={{ status: 'STARTING', ...QUEUED_JOB }} />
    );

    expect(screen.getByText('Waiting for quota')).toBeInTheDocument();
    expect(screen.getByText('40m so far')).toBeInTheDocument();
  });

  it('marks the phase that is still running as unfinished', () => {
    // Its number is growing. Drawn like the settled ones it claims a
    // measurement that has not been taken -- and "12s" beside a phase the job
    // is still in reads as the phase having ended.
    render(<JobStartupTimeline jobData={QUEUED_JOB} />);

    expect(screen.getByTestId('startup-segment-queue_wait')).toHaveAttribute(
      'data-open',
      'true'
    );
    expect(
      screen.getByTestId('startup-segment-controller_queue')
    ).not.toHaveAttribute('data-open');
  });

  it('does not offer an end timestamp for a job that has not started', () => {
    render(
      <JobStartupTimeline
        jobData={{
          ...QUEUED_JOB,
          eligible_at: new Date('2026-09-16T09:23:14Z'),
        }}
      />
    );

    expect(screen.getByText(/^since /)).toBeInTheDocument();
    expect(screen.queryByText(/ to /)).not.toBeInTheDocument();
  });

  it('prefers the settled breakdown once the job has one', () => {
    // Both can be present only if the server computed progress for a job that
    // has since started. The stored numbers are the ones that stopped moving.
    render(
      <JobStartupTimeline
        jobData={{
          ...GATED_JOB,
          startup_progress: QUEUED_JOB.startup_progress,
        }}
      />
    );

    expect(screen.getByText('16m 40s')).toBeInTheDocument();
    expect(screen.queryByText(/so far/)).not.toBeInTheDocument();
  });

  it('shows phases that add up to the total beside them', () => {
    // Reported from the tenant: a 62.4s job printed 10s + 1s + 49s + 0s + 0s
    // under a headline of "1m 2s". Each phase was floored on its own, so five
    // of them lost two whole seconds against a total floored once -- and a
    // breakdown whose parts visibly do not make the whole is not believed,
    // however exactly the underlying numbers sum.
    render(
      <JobStartupTimeline
        jobData={{
          t_time_to_running: 62.4,
          t_controller_queue: 10.4,
          t_provision_setup: 1.6,
          t_queue_wait: 49.7,
          t_node_startup: 0.4,
          t_runtime_setup: 0.3,
        }}
      />
    );

    const legend = screen
      .getAllByText(/^(<1s|\d+m \d+s|\d+s)$/)
      .map((el) => el.textContent);
    const seconds = legend.map((text) => {
      if (text === '<1s') return 0;
      const m = text.match(/(?:(\d+)m )?(\d+)s/);
      return Number(m[1] || 0) * 60 + Number(m[2]);
    });
    // The header is one of these; the rest are the phases.
    const total = Math.max(...seconds);
    const parts = seconds.filter((v) => v !== total);
    expect(total).toBe(62);
    expect(parts.reduce((a, b) => a + b, 0)).toBe(62);
  });

  it('calls a sub-second phase short rather than zero', () => {
    // The panel drops phases that really are zero, so "0s" on a phase that did
    // happen reads as one of those -- and it sat next to a visible sliver of
    // colour, which is the contradiction the tenant spotted.
    render(
      <JobStartupTimeline
        jobData={{
          t_time_to_running: 62.4,
          t_controller_queue: 10.4,
          t_provision_setup: 1.6,
          t_queue_wait: 49.7,
          t_node_startup: 0.4,
          t_runtime_setup: 0.3,
        }}
      />
    );

    expect(screen.queryByText('0s')).not.toBeInTheDocument();
  });

  it('still names the phase a job is in before it has accumulated a second', () => {
    // The header says "Starting" and the one thing a reader wants from it is
    // which phase that means. Filtering on seconds > 0 alone made that vanish
    // for as long as the open phase had no measurable width -- saying a job
    // was starting while refusing to say at what.
    render(
      <JobStartupTimeline
        jobData={{
          startup_progress: {
            total: 12,
            phases: { controller_queue: 12, provision_setup: 0 },
            open_phase: 'provision_setup',
          },
        }}
      />
    );

    expect(screen.getByText('Preparing the launch')).toBeInTheDocument();
    expect(screen.getByText('<1s so far')).toBeInTheDocument();
  });

  it('renders nothing for a job with no recorded timeline', () => {
    // Jobs that never started, and jobs launched before the timeline existed.
    // An empty bar would read as "started instantly".
    const { container } = render(
      <JobStartupTimeline jobData={{ status: 'PENDING' }} />
    );

    expect(container).toBeEmptyDOMElement();
  });
});
