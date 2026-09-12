import React from 'react';
import { NonCapitalizedTooltip as Tooltip } from '@/components/utils';

/**
 * A compact badge marking a dynamic task: a managed job launched from inside
 * a job group (with `sky jobs launch`) that is shown under the group as one
 * of its tasks. Styled like PrimaryBadge. `launchedFrom` completes the
 * hover text, e.g. "task 1 of job 66".
 */
export const DynamicBadge = ({
  launchedFrom = null,
  showTooltip = true,
  className = '',
}) => {
  const badge = (
    <span
      className={`
        inline-flex items-center
        px-1.5 py-0.5
        text-[10px] font-semibold uppercase tracking-wide
        bg-gradient-to-r from-violet-50 to-indigo-50
        text-violet-700
        border border-violet-200
        rounded
        shadow-sm
        cursor-help
        select-none
        ${className}
      `}
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
          : 'Dynamic task launched from inside the job group'
      }
      className="text-muted-foreground"
    >
      {badge}
    </Tooltip>
  );
};

export default DynamicBadge;
