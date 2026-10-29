import React from 'react';

/**
 * Segmented control for switching the scope of a table view
 * (e.g. Active/All activity, My/All ownership). Sized to sit inline
 * with the dashboard's compact filter-row controls (status chips,
 * Refresh link) — text-sm labels on a gray track with a white
 * selected segment.
 *
 * The selected segment is the single source of truth for the current
 * scope — pair it with a scope-aware empty state rather than a separate
 * "showing X only" hint.
 *
 * @param {string} ariaLabel - Accessible label for the tablist.
 * @param {Array<{value: string, label: string}>} options - Segments.
 * @param {string|null} value - Currently selected segment value. May match
 *   no option (e.g. an explicit filter has overridden the toggle), in which
 *   case no segment is highlighted.
 * @param {Function} onChange - Called with the clicked segment's value.
 */
export function SegmentedToggle({ ariaLabel, options, value, onChange }) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="inline-flex items-center bg-gray-100 rounded-lg p-0.5 shrink-0"
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(option.value)}
            className={`px-3 py-1 rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 ${
              selected
                ? 'bg-white text-gray-900 shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
