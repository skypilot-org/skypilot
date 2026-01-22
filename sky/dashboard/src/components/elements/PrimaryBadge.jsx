import React from 'react';
import { NonCapitalizedTooltip as Tooltip } from '@/components/utils';

/**
 * A compact badge indicating a primary task in a job group.
 * Primary tasks determine the overall job group status - when all primary
 * tasks complete, auxiliary tasks are automatically terminated.
 */
export const PrimaryBadge = ({ showTooltip = true, className = '' }) => {
  const badge = (
    <span
      className={`
        inline-flex items-center gap-0.5
        px-1.5 py-0.5
        text-[10px] font-semibold uppercase tracking-wide
        bg-gradient-to-r from-emerald-50 to-teal-50
        text-emerald-700
        border border-emerald-200
        rounded
        shadow-sm
        cursor-help
        select-none
        ${className}
      `}
    >
      {/* Star icon - simple SVG for "primary" concept */}
      <svg
        className="w-2.5 h-2.5 text-emerald-500"
        viewBox="0 0 20 20"
        fill="currentColor"
        aria-hidden="true"
      >
        <path
          fillRule="evenodd"
          d="M10.868 2.884c-.321-.772-1.415-.772-1.736 0l-1.83 4.401-4.753.381c-.833.067-1.171 1.107-.536 1.651l3.62 3.102-1.106 4.637c-.194.813.691 1.456 1.405 1.02L10 15.591l4.069 2.485c.713.436 1.598-.207 1.404-1.02l-1.106-4.637 3.62-3.102c.635-.544.297-1.584-.536-1.65l-4.752-.382-1.831-4.401z"
          clipRule="evenodd"
        />
      </svg>
      <span>Primary</span>
    </span>
  );

  if (!showTooltip) {
    return badge;
  }

  return (
    <Tooltip
      content="Primary task – other tasks will be terminated once all primary tasks finish"
      className="text-muted-foreground"
    >
      {badge}
    </Tooltip>
  );
};

export default PrimaryBadge;
