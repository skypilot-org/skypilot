/**
 * Where a job's time between submission and running went.
 *
 * The phases have different owners and opposite fixes -- a long
 * controller_queue means our scheduler is saturated, a long queue_wait means
 * the cluster is out of quota -- so the point of showing them is to say who
 * should go and look, not merely that starting was slow.
 *
 * Two sources, one bar. A job that has started carries the settled breakdown
 * in its t_* columns. One that has not carries `startup_progress`, computed
 * per request from the same milestones, where the last phase is still running
 * -- which is the state someone opens this page to look at.
 */
import PropTypes from 'prop-types';
import React from 'react';

import { formatDuration, formatFullTimestamp } from '@/components/utils';

// In the order they happen, so the bar reads left to right as the job's life.
// Keyed by the phase's own name; the settled column is that name under `t_`,
// which is the mapping the recorder uses (launch_phases._JOB_PHASE_COLUMNS,
// guarded by test_the_column_of_a_phase_is_its_name_under_t).
const PHASES = [
  {
    key: 'controller_queue',
    label: 'Waiting for a controller',
    hint: 'The jobs controller had no free slot yet.',
    color: 'bg-slate-400',
  },
  {
    key: 'retry_overhead',
    label: 'Retried launches',
    hint: 'Launch attempts that were thrown away, plus the backoff between them.',
    color: 'bg-amber-400',
  },
  {
    key: 'unattributed',
    label: 'Unaccounted',
    hint: 'Time no phase claims -- a job placed on a warm pool never provisions, and one that started before this breakdown existed has no attempt recorded.',
    color: 'bg-gray-300',
  },
  {
    key: 'provision_setup',
    label: 'Preparing the launch',
    hint: 'Resolving resources, uploading files, and asking for the instances.',
    color: 'bg-sky-300',
  },
  {
    key: 'queue_wait',
    label: 'Waiting for quota',
    hint: 'The workload sat in an external scheduler queue until it was admitted.',
    color: 'bg-rose-400',
  },
  {
    key: 'node_startup',
    label: 'Starting the nodes',
    hint: 'Scale-up, image pulls, and containers reaching Running.',
    color: 'bg-indigo-400',
  },
  {
    key: 'runtime_setup',
    label: 'Setting up the job',
    hint: 'SkyPilot runtime, file mounts, and your setup commands.',
    color: 'bg-emerald-400',
  },
];

/**
 * The settled breakdown, or the live one, or nothing.
 *
 * Settled wins where both exist: the server only computes the live one for a
 * job with no start_at, so the two cannot describe the same job, but reading
 * the stored columns first keeps a finished job's numbers stable.
 */
function readTimeline(jobData) {
  if (jobData?.t_time_to_running > 0) {
    return {
      total: jobData.t_time_to_running,
      seconds: (key) => jobData[`t_${key}`] || 0,
      openPhase: null,
    };
  }
  const progress = jobData?.startup_progress;
  if (progress?.total > 0 && progress.phases) {
    return {
      total: progress.total,
      seconds: (key) => progress.phases[key] || 0,
      openPhase: progress.open_phase ?? null,
    };
  }
  return null;
}

/**
 * Whole seconds for each phase that still add up to the whole seconds of the
 * total.
 *
 * formatDuration floors, so formatting each phase on its own loses up to a
 * second per phase against a total that is floored once: a 62.4s job split
 * five ways printed 10s + 1s + 49s + 0s + 0s beside a headline of 1m 2s, and a
 * breakdown whose parts visibly do not make the whole is not believed, however
 * exactly the underlying numbers sum.
 *
 * Largest remainder: floor everything, then hand the seconds that were lost to
 * the phases that lost the most of one. The bar itself is drawn from the raw
 * values and is unaffected.
 */
function apportionSeconds(values, total) {
  const floors = values.map((value) => Math.floor(value));
  const out = floors.slice();
  let left = Math.floor(total) - floors.reduce((sum, v) => sum + v, 0);
  const byRemainder = values
    .map((value, index) => ({ index, part: value - Math.floor(value) }))
    .sort((a, b) => b.part - a.part);
  // The phases sum to the total, so `left` is normally in [0, phases). It is
  // still handled in both directions: the settled columns are stored
  // separately and nothing forces them to agree to the last float.
  for (let i = 0; left > 0 && i < byRemainder.length; i++, left--) {
    out[byRemainder[i].index] += 1;
  }
  for (let i = byRemainder.length - 1; left < 0 && i >= 0; i--) {
    if (out[byRemainder[i].index] > 0) {
      out[byRemainder[i].index] -= 1;
      left++;
    }
  }
  return out;
}

// A phase too short to show a second of its own. Not "0s": the panel drops
// phases that really are zero, so a zero here would read as one of those --
// and it is the shape of the bar, not the number, that says it was brief.
function label(seconds, open) {
  const text = seconds === 0 ? '<1s' : formatDuration(seconds);
  return open ? `${text} so far` : text;
}

/**
 * Whether the *job* page draws the bar, as opposed to the task page.
 *
 * Every field the job page shows comes from its first task, and for a pipeline
 * that is one task of several measured from its own handoff rather than from
 * the job's submission -- so task 0's bar under the job's name would be one
 * task's number presented as the job's. Those get the panel per task instead.
 *
 * A job group is the exception the task count cannot see: its tasks all become
 * eligible at the same moment, so task 0's bar really is the job's.
 *
 * Here rather than inline in the page so it can be tested without rendering
 * the page, which is the only reason the condition had no test beside it.
 */
export function jobPageShowsTimeline(taskCount, jobData) {
  return taskCount <= 1 || Boolean(jobData?.is_job_group);
}

export function JobStartupTimeline({ jobData }) {
  const timeline = readTimeline(jobData);
  // Absent for jobs that never started, and for those launched before the
  // timeline was recorded. Showing an empty bar would read as "instant".
  if (timeline === null) {
    return null;
  }

  const { total, openPhase } = timeline;
  const inFlight = openPhase !== null;
  // The bar's own ends. Absent on jobs recorded before these were carried, in
  // which case the bar still draws and only the timestamps are omitted.
  const from = jobData?.eligible_at;
  const to = jobData?.started_at ?? jobData?.start_at;

  // Phases that did not happen are dropped -- a zero-width "Retried launches"
  // would say the job retried. The open phase is kept even at zero: the header
  // says "Starting", and the one thing a reader wants from it is which phase
  // that means, which would otherwise vanish for as long as the phase has yet
  // to accumulate a measurable moment.
  const measured = PHASES.map((phase) => ({
    ...phase,
    seconds: timeline.seconds(phase.key),
    open: phase.key === openPhase,
  })).filter((segment) => segment.seconds > 0 || segment.open);
  const shown = apportionSeconds(
    measured.map((segment) => segment.seconds),
    total
  );
  const segments = measured.map((segment, index) => ({
    ...segment,
    shown: shown[index],
  }));

  if (segments.length === 0) {
    return null;
  }

  return (
    <div className="mb-6">
      <div className="flex items-baseline justify-between">
        <div className="text-gray-600 font-medium text-base">
          {inFlight ? 'Starting' : 'Time to start'}
        </div>
        <div className="text-base text-gray-700">
          {formatDuration(total)}
          {inFlight ? ' so far' : ''}
        </div>
      </div>

      {/* Both ends of the bar, so it closes over itself.
       *
       * The page disagreed with itself without this: the bar spans
       * eligible_at -> start_at, while the grid shows submitted_at as
       * "Submitted", so subtracting the two visible timestamps gave a third
       * number, short by exactly the controller wait.
       *
       * The left end is eligible_at, not created_at. They are the same
       * moment for a single task and for every task of a job group, but a
       * pipeline's task N becomes eligible when task N-1 finishes -- showing
       * creation there would put the whole upstream runtime between the
       * timestamp and the bar, which is the same bug on another page.
       *
       * In flight there is no right end yet, and the elapsed side is as of
       * the last fetch rather than now -- so it is named by the phase the job
       * is in rather than by a clock that would look live and not be. */}
      {from && (inFlight || to) ? (
        <div className="mt-0.5 text-sm text-gray-500">
          {inFlight
            ? `since ${formatFullTimestamp(from)}`
            : `from ${formatFullTimestamp(from)} to ${formatFullTimestamp(to)}`}
        </div>
      ) : null}

      <div className="mt-2 flex h-3 w-full overflow-hidden rounded bg-gray-100">
        {segments.map((segment) => (
          <div
            key={segment.key}
            // The open segment is still growing, so it is hatched rather than
            // solid: a flat block of colour claims a measurement that has not
            // finished being taken.
            className={`${segment.color}${segment.open ? ' opacity-60' : ''}`}
            style={{
              width: `${(segment.seconds / total) * 100}%`,
              ...(segment.open
                ? {
                    backgroundImage:
                      'repeating-linear-gradient(45deg, rgba(255,255,255,0.55) 0 4px, transparent 4px 8px)',
                  }
                : {}),
            }}
            // The tooltip carries the measurement itself, to a tenth, since
            // the legend shows whole seconds that have been apportioned.
            title={`${segment.label}: ${segment.seconds.toFixed(1)}s${
              segment.open ? ' so far' : ''
            } — ${segment.hint}`}
            data-testid={`startup-segment-${segment.key}`}
            data-open={segment.open ? 'true' : undefined}
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
              {label(segment.shown, segment.open)}
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
