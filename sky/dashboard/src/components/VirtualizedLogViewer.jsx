import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react';
import PropTypes from 'prop-types';

const LINE_HEIGHT = 20; // pixels per line
const OVERSCAN = 10; // extra lines to render above/below viewport

/**
 * A virtualized log viewer that only renders visible lines.
 * This prevents browser crashes when displaying thousands of log lines.
 */
export function VirtualizedLogViewer({
  lines,
  maxHeight = 384, // 24rem = 384px
  className = '',
  autoScroll = true,
}) {
  const containerRef = useRef(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [isUserScrolling, setIsUserScrolling] = useState(false);
  const prevLinesLength = useRef(lines.length);

  // Calculate visible range
  const visibleRange = useMemo(() => {
    const visibleLines = Math.ceil(maxHeight / LINE_HEIGHT);
    const startIndex = Math.max(0, Math.floor(scrollTop / LINE_HEIGHT) - OVERSCAN);
    const endIndex = Math.min(
      lines.length,
      Math.ceil((scrollTop + maxHeight) / LINE_HEIGHT) + OVERSCAN
    );
    return { startIndex, endIndex, visibleLines };
  }, [scrollTop, maxHeight, lines.length]);

  // Handle scroll events
  const handleScroll = useCallback((e) => {
    const newScrollTop = e.target.scrollTop;
    setScrollTop(newScrollTop);
    lastScrollTime.current = Date.now();

    // Check if user is scrolling up (not at bottom)
    const isAtBottom =
      e.target.scrollHeight - e.target.scrollTop - e.target.clientHeight < 50;
    setIsUserScrolling(!isAtBottom);
  }, []);

  // Auto-scroll to bottom when new lines arrive (if not user scrolling)
  useEffect(() => {
    if (!containerRef.current || !autoScroll) return;

    const hasNewLines = lines.length > prevLinesLength.current;
    prevLinesLength.current = lines.length;

    if (hasNewLines && !isUserScrolling) {
      // Small delay to ensure DOM has updated
      requestAnimationFrame(() => {
        if (containerRef.current) {
          containerRef.current.scrollTop = containerRef.current.scrollHeight;
        }
      });
    }
  }, [lines.length, autoScroll, isUserScrolling]);

  // Reset user scrolling flag when they scroll to bottom
  useEffect(() => {
    if (!containerRef.current) return;

    const container = containerRef.current;
    const checkIfAtBottom = () => {
      const isAtBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight < 50;
      if (isAtBottom) {
        setIsUserScrolling(false);
      }
    };

    // Check periodically in case scroll position changed
    const interval = setInterval(checkIfAtBottom, 1000);
    return () => clearInterval(interval);
  }, []);

  const totalHeight = lines.length * LINE_HEIGHT;
  const { startIndex, endIndex } = visibleRange;

  // Only render visible lines
  const visibleLines = useMemo(() => {
    return lines.slice(startIndex, endIndex).map((line, index) => (
      <div
        key={startIndex + index}
        style={{
          position: 'absolute',
          top: (startIndex + index) * LINE_HEIGHT,
          left: 0,
          right: 0,
          height: LINE_HEIGHT,
          lineHeight: `${LINE_HEIGHT}px`,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
        className="px-2"
      >
        {line}
      </div>
    ));
  }, [lines, startIndex, endIndex]);

  if (lines.length === 0) {
    return (
      <div
        className={`bg-[#f7f7f7] flex items-center justify-center py-4 text-gray-500 ${className}`}
        style={{ maxHeight }}
      >
        No logs available
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className={`bg-[#f7f7f7] font-mono text-sm text-gray-900 overflow-auto ${className}`}
      style={{ maxHeight, position: 'relative' }}
      aria-label="job-logs"
    >
      <div
        style={{
          height: totalHeight,
          position: 'relative',
          minHeight: '100%',
        }}
      >
        {visibleLines}
      </div>
    </div>
  );
}

VirtualizedLogViewer.propTypes = {
  lines: PropTypes.arrayOf(PropTypes.string).isRequired,
  maxHeight: PropTypes.number,
  className: PropTypes.string,
  autoScroll: PropTypes.bool,
};

export default VirtualizedLogViewer;
