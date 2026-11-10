import React from 'react';
import { NonCapitalizedTooltip as Tooltip } from '@/components/utils';

/**
 * A compact badge marking a dynamic task: a managed job launched from inside
 * a job group (with `sky jobs launch`) that is shown under the group as one
 * of its tasks. Styled like the JobGroup / Batch pills (gray, no colour).
 * `launchedFrom` names the launching task ("task 1 of job 66"); null means
 * the job was attached explicitly (`--job-group`) with no launching task,
 * and the hover says so. It completes the
 * hover text, e.g. "task 1 of job 66".
 */
export const DynamicBadge = ({
  launchedFrom = null,
  showTooltip = true,
  className = '',
}) => {
  const badge = (
    <span
      className={`px-2 py-0.5 rounded text-xs font-medium bg-gray-200 text-gray-700 whitespace-nowrap cursor-help select-none ${className}`}
    >
      Dynamic
    </span>
  );

  if (!showTooltip) {
    return badge;
  }

  return (
    <Tooltip
      content={
        launchedFrom
          ? `Dynamic task launched from ${launchedFrom}`
          : 'Dynamic task attached to the job group from outside it'
      }
      className="text-muted-foreground"
    >
      {badge}
    </Tooltip>
  );
};

export default DynamicBadge;
