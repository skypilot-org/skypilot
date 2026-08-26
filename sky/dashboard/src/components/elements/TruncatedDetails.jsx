'use client';

import React, { useRef } from 'react';
import { TableRow, TableCell } from '@/components/ui/table';

const TOGGLE_SELECTOR = '[data-button-type="show-more-less"]';

// A table that collapses the expanded row on an outside click must not count
// the toggle itself as outside: its mousedown would collapse the row, and the
// click that follows would re-expand it, so "show less" would never close.
export function isDetailsToggle(target) {
  return Boolean(target?.closest?.(TOGGLE_SELECTOR));
}

export function ExpandedDetailsRow({ text, colSpan, innerRef }) {
  return (
    <TableRow className="expanded-details">
      <TableCell colSpan={colSpan}>
        <div
          className="p-4 bg-gray-50 rounded-md border border-gray-200"
          ref={innerRef}
        >
          <div className="flex justify-between items-start">
            <div className="flex-1">
              <p className="text-sm font-medium text-gray-900">Full Details</p>
              <p
                className="mt-1 text-sm text-gray-700"
                style={{ whiteSpace: 'pre-wrap' }}
              >
                {text}
              </p>
            </div>
          </div>
        </div>
      </TableCell>
    </TableRow>
  );
}

export function TruncatedDetails({
  text,
  rowId,
  expandedRowId,
  setExpandedRowId,
}) {
  const safeText = text || '';
  const isTruncated = safeText.length > 50;
  const isExpanded = expandedRowId === rowId;
  // Always show truncated text in the table cell
  const displayText = isTruncated ? `${safeText.substring(0, 50)}` : safeText;
  const buttonRef = useRef(null);

  const handleClick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setExpandedRowId(isExpanded ? null : rowId);
  };

  return (
    <div className="truncated-details relative max-w-full flex items-center">
      <span className="truncate">{displayText}</span>
      {isTruncated && (
        <button
          ref={buttonRef}
          type="button"
          onClick={handleClick}
          className="text-blue-600 hover:text-blue-800 font-medium ml-1 flex-shrink-0"
          // isDetailsToggle finds the button by this attribute.
          data-button-type="show-more-less"
        >
          {isExpanded ? '... show less' : '... show more'}
        </button>
      )}
    </div>
  );
}
