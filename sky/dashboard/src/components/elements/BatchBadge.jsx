import React from 'react';
import { NonCapitalizedTooltip as Tooltip } from '@/components/utils';

/**
 * A compact badge indicating a batch inference job (ds.map()).
 * Styled to match the JobGroup badge format.
 */
export const BatchBadge = ({ showTooltip = true, className = '' }) => {
  const badge = (
    <span
      className={`text-xs font-medium bg-surface-strong text-content px-1.5 py-0.5 rounded whitespace-nowrap ${className}`}
    >
      Batch
    </span>
  );

  if (!showTooltip) {
    return badge;
  }

  return (
    <Tooltip
      content="Batch inference job – processes data in parallel batches across workers"
      className="text-muted-foreground"
    >
      {badge}
    </Tooltip>
  );
};

export default BatchBadge;
