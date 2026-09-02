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
    expect(screen.getByTestId('startup-segment-t_queue_wait')).toHaveStyle(
      'width: 60%'
    );
    expect(screen.getByTestId('startup-segment-t_node_startup')).toHaveStyle(
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

    expect(screen.getByTestId('startup-segment-t_unattributed')).toHaveStyle(
      'width: 99%'
    );
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
