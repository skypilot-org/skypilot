'use client';

import React, { useEffect, useRef, useState } from 'react';
import { TableRow, TableCell } from '@/components/ui/table';
import { LinkifiedText } from '@/components/elements/LinkifiedText';

const TOGGLE_SELECTOR = '[data-button-type="show-more-less"]';

// A table that collapses the expanded row on an outside click must not count
// the toggle itself as outside: its mousedown would collapse the row, and the
// click that follows would re-expand it, so "show less" would never close.
export function isDetailsToggle(target) {
  return Boolean(target?.closest?.(TOGGLE_SELECTOR));
}

// Matches the cell's p-4 padding, so the panel keeps its inset when pinned.
const PANEL_INSET_PX = 16;

function findHorizontalScroller(node) {
  for (let el = node?.parentElement; el; el = el.parentElement) {
    const { overflowX } = window.getComputedStyle(el);
    if (overflowX === 'auto' || overflowX === 'scroll') return el;
  }
  return null;
}

export function ExpandedDetailsRow({ text, colSpan, innerRef }) {
  const cellRef = useRef(null);
  const [visibleWidth, setVisibleWidth] = useState(null);

  // The cell spans the whole table, which can be wider than its scroll
  // container. Users open this row from the Details column on the right, so
  // without pinning, the panel starts at the table's left edge, off screen.
  // Pin it to the visible part of the scroller instead.
  useEffect(() => {
    const scroller = findHorizontalScroller(cellRef.current);
    if (!scroller) return undefined;
    const update = () =>
      setVisibleWidth(Math.max(scroller.clientWidth - 2 * PANEL_INSET_PX, 0));
    update();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update);
      return () => window.removeEventListener('resize', update);
    }
    const observer = new ResizeObserver(update);
    observer.observe(scroller);
    return () => observer.disconnect();
  }, []);

  return (
    <TableRow className="expanded-details">
      <TableCell colSpan={colSpan} ref={cellRef}>
        <div
          className="p-4 bg-gray-50 rounded-md border border-gray-200"
          ref={innerRef}
          style={
            visibleWidth === null
              ? undefined
              : {
                  position: 'sticky',
                  left: PANEL_INSET_PX,
                  width: visibleWidth,
                  boxSizing: 'border-box',
                }
          }
        >
          <div className="flex justify-between items-start">
            <div className="flex-1">
              <p className="text-sm font-medium text-gray-900">Full Details</p>
              <p
                className="mt-1 text-sm text-gray-700"
                style={{ whiteSpace: 'pre-wrap' }}
              >
                {/* Details can carry a remediation hint with a docs URL; make
                    it followable now that the full text is on screen. */}
                <LinkifiedText text={text} />
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
