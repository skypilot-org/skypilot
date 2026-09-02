/**
 * Where a job's time between submission and running went.
 *
 * The phases have different owners and opposite fixes -- a long
 * controller_queue means our scheduler is saturated, a long queue_wait means
 * the cluster is out of quota -- so the point of showing them is to say who
 * should go and look, not merely that starting was slow.
 */
import PropTypes from 'prop-types';
import React from 'react';

import { formatDuration } from '@/components/utils';

// In the order they happen, so the bar reads left to right as the job's life.
const PHASES = [
  {
    key: 't_controller_queue',
    label: 'Waiting for a controller',
    hint: 'The jobs controller had no free slot yet.',
    color: 'bg-slate-400',
  },
  {
    key: 't_retry_overhead',
    label: 'Retried launches',
    hint: 'Launch attempts that were thrown away, plus the backoff between them.',
    color: 'bg-amber-400',
  },
  {
    key: 't_provision_setup',
    label: 'Preparing the launch',
    hint: 'Resolving resources, uploading files, and asking for the instances.',
    color: 'bg-sky-300',
  },
  {
    key: 't_queue_wait',
    label: 'Waiting for quota',
    hint: 'The workload sat in an external scheduler queue until it was admitted.',
    color: 'bg-rose-400',
  },
  {
    key: 't_node_startup',
    label: 'Starting the nodes',
    hint: 'Scale-up, image pulls, and containers reaching Running.',
    color: 'bg-indigo-400',
  },
  {
    key: 't_runtime_setup',
    label: 'Setting up the job',
    hint: 'SkyPilot runtime, file mounts, and your setup commands.',
    color: 'bg-emerald-400',
  },
];

export function JobStartupTimeline({ jobData }) {
  const total = jobData?.t_time_to_running;
  // Absent for jobs that never started, and for those launched before the
  // timeline was recorded. Showing an empty bar would read as "instant".
  if (!total || total <= 0) {
    return null;
  }

  const segments = PHASES.map((phase) => ({
    ...phase,
    seconds: jobData[phase.key] || 0,
  })).filter((segment) => segment.seconds > 0);

  if (segments.length === 0) {
    return null;
  }

  return (
    <div className="mb-6">
      <div className="flex items-baseline justify-between">
        <div className="text-gray-600 font-medium text-base">Time to start</div>
        <div className="text-base text-gray-700">{formatDuration(total)}</div>
      </div>

      <div className="mt-2 flex h-3 w-full overflow-hidden rounded bg-gray-100">
        {segments.map((segment) => (
          <div
            key={segment.key}
            className={segment.color}
            style={{ width: `${(segment.seconds / total) * 100}%` }}
            title={`${segment.label}: ${formatDuration(segment.seconds)} — ${segment.hint}`}
            data-testid={`startup-segment-${segment.key}`}
          />
        ))}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {segments.map((segment) => (
          <div key={segment.key} className="flex items-center text-sm">
            <span
              className={`mr-1.5 inline-block h-2 w-2 rounded-sm ${segment.color}`}
            />
            <span className="text-gray-600">{segment.label}</span>
            <span className="ml-1.5 text-gray-900">
              {formatDuration(segment.seconds)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

JobStartupTimeline.propTypes = {
  jobData: PropTypes.object,
};
