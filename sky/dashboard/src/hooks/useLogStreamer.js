import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { stripAnsiCodes, shouldDropLogLine } from '@/components/utils';

// Performance constants
const DEFAULT_MAX_RENDER_LINES = 1000; // Reduced from 5000 to prevent browser crashes
const WARNING_THRESHOLD = 0.8; // Warn at 80% capacity

/**
 * Shared log streaming hook used by both managed job and cluster pages.
 * @param {Object} options
 * @param {Function} options.streamFn - The streaming function to call
 * @param {Object} options.streamArgs - Arguments to pass to the stream function
 * @param {boolean} options.enabled - Whether streaming is enabled
 * @param {number} options.refreshTrigger - Incrementing this restarts the stream
 * @param {boolean} options.follow - If true, stream continuously (live streaming)
 * @param {number} options.maxLineChars - Max characters per line before truncation
 * @param {number} options.maxRenderLines - Max lines to keep in memory
 * @param {number} options.flushIntervalMs - How often to flush buffered lines to state
 * @param {Function} options.onError - Error callback
 * @param {Function} options.onMaxLinesReached - Called when max lines reached (for auto-pause)
 */
export function useLogStreamer({
  streamFn,
  streamArgs,
  enabled = true,
  refreshTrigger = 0,
  follow = false,
  maxLineChars = 2000,
  maxRenderLines = DEFAULT_MAX_RENDER_LINES,
  flushIntervalMs = 100,
  onError = (error) => {
    // mark parameter as used to satisfy lint while keeping signature
    void error;
  },
  onMaxLinesReached = () => {},
}) {
  const [logLines, setLogLines] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [hasReceivedFirstChunk, setHasReceivedFirstChunk] = useState(false);
  const [progressTick, setProgressTick] = useState(0);
  const [isPausedByVisibility, setIsPausedByVisibility] = useState(false);
  const [isAtMaxLines, setIsAtMaxLines] = useState(false);

  const bufferRef = useRef([]);
  const partialLineRef = useRef('');
  const progressMapRef = useRef(new Map());
  const flushTimerRef = useRef(null);
  const controllerRef = useRef(null);
  const hasFirstChunkRef = useRef(false);
  const onErrorRef = useRef(onError);
  const onMaxLinesReachedRef = useRef(onMaxLinesReached);
  const maxLinesNotifiedRef = useRef(false);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  useEffect(() => {
    onMaxLinesReachedRef.current = onMaxLinesReached;
  }, [onMaxLinesReached]);

  // Auto-pause streaming when tab is hidden (performance optimization)
  useEffect(() => {
    if (!follow) return;

    const handleVisibilityChange = () => {
      if (document.hidden) {
        setIsPausedByVisibility(true);
        // Abort the current stream when tab is hidden
        if (controllerRef.current) {
          controllerRef.current.abort();
        }
      } else {
        setIsPausedByVisibility(false);
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [follow]);

  const resetState = useCallback(() => {
    bufferRef.current = [];
    partialLineRef.current = '';
    progressMapRef.current = new Map();
    hasFirstChunkRef.current = false;
    maxLinesNotifiedRef.current = false;
    setLogLines([]);
    setHasReceivedFirstChunk(false);
    setIsAtMaxLines(false);
  }, []);

  // Clear logs function for manual clearing
  const clearLogs = useCallback(() => {
    bufferRef.current = [];
    progressMapRef.current = new Map();
    maxLinesNotifiedRef.current = false;
    setLogLines([]);
    setIsAtMaxLines(false);
  }, []);

  const displayLines = useMemo(
    () => [...logLines, ...Array.from(progressMapRef.current.values())],
    [logLines, progressTick]
  );

  const flushBufferedLines = useCallback(() => {
    if (bufferRef.current.length === 0) return;
    setLogLines((prev) => {
      const next = [...prev, ...bufferRef.current];
      bufferRef.current = [];

      // Check if we've hit max lines
      if (next.length >= maxRenderLines) {
        setIsAtMaxLines(true);
        // Notify once when max lines reached
        if (!maxLinesNotifiedRef.current && follow) {
          maxLinesNotifiedRef.current = true;
          // Call the callback asynchronously to avoid state update during render
          setTimeout(() => {
            const cb = onMaxLinesReachedRef.current;
            if (cb) cb();
          }, 0);
        }
      }

      return next.length > maxRenderLines
        ? next.slice(next.length - maxRenderLines)
        : next;
    });
  }, [maxRenderLines, follow]);

  useEffect(() => {
    let active = true;
    resetState();

    // Don't start streaming if disabled, paused by visibility, or no stream function
    if (!enabled || !streamFn || (follow && isPausedByVisibility)) {
      setIsLoading(false);
      return () => {};
    }

    const controller = new AbortController();
    controllerRef.current = controller;

    const scheduleFlush = () => {
      if (flushTimerRef.current) return;
      flushTimerRef.current = setTimeout(() => {
        flushTimerRef.current = null;
        flushBufferedLines();
      }, flushIntervalMs);
    };

    const appendProgressLine = (line) => {
      const processMatch = line.match(/^\(([^)]+)\)/);
      if (processMatch) {
        progressMapRef.current.set(processMatch[1], line);
        setProgressTick((tick) => tick + 1);
        return;
      }
      // Some progress bars (e.g. data processing) do not include a process
      // prefix; fall back to treating them as regular log lines so they render.
      bufferRef.current.push(line);
      if (bufferRef.current.length > maxRenderLines * 2) {
        bufferRef.current = bufferRef.current.slice(
          bufferRef.current.length - maxRenderLines
        );
      }
    };

    const processChunk = (chunk) => {
      const parts = chunk.split('\n');
      parts[0] = partialLineRef.current + parts[0];
      const endsWithNewline = chunk.endsWith('\n');
      partialLineRef.current = endsWithNewline ? '' : parts.pop() || '';

      const newLines = parts.filter((line) => line.trim());
      if (!hasFirstChunkRef.current && newLines.length > 0) {
        hasFirstChunkRef.current = true;
        setHasReceivedFirstChunk(true);
      }

      for (const line of newLines) {
        let cleanLine = stripAnsiCodes(line);
        if (shouldDropLogLine(cleanLine)) {
          continue;
        }
        if (cleanLine.length > maxLineChars) {
          cleanLine = cleanLine.slice(0, maxLineChars) + ' … [truncated]';
        }

        const isProgressBar = /\d+%\s*\|/.test(cleanLine);
        if (isProgressBar) {
          appendProgressLine(cleanLine);
        } else {
          bufferRef.current.push(cleanLine);
          if (bufferRef.current.length > maxRenderLines * 2) {
            bufferRef.current = bufferRef.current.slice(
              bufferRef.current.length - maxRenderLines
            );
          }
        }
      }
      scheduleFlush();
    };

    setIsLoading(true);

    streamFn({
      ...streamArgs,
      follow,
      onNewLog: (chunk) => {
        if (active) {
          processChunk(chunk);
        }
      },
      signal: controller.signal,
    })
      .then(() => {
        if (!active) return;
        if (partialLineRef.current) {
          bufferRef.current.push(partialLineRef.current);
          partialLineRef.current = '';
        }
        flushBufferedLines();
        setIsLoading(false);
      })
      .catch((error) => {
        if (!active || error.name === 'AbortError') return;
        const onErrorCb = onErrorRef.current;
        if (onErrorCb) {
          onErrorCb(error);
        }
        setLogLines((prev) => [
          ...prev,
          `Error fetching logs: ${error.message}`,
        ]);
        setIsLoading(false);
      });

    return () => {
      active = false;
      if (flushTimerRef.current) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      if (controllerRef.current) {
        controllerRef.current.abort();
        controllerRef.current = null;
      }
      bufferRef.current = [];
      partialLineRef.current = '';
      progressMapRef.current.clear();
    };
  }, [
    streamFn,
    streamArgs,
    enabled,
    refreshTrigger,
    follow,
    isPausedByVisibility,
    flushBufferedLines,
    flushIntervalMs,
    maxLineChars,
    maxRenderLines,
    resetState,
  ]);

  // Calculate warning state
  const lineCount = displayLines.length;
  const isNearMaxLines = lineCount >= maxRenderLines * WARNING_THRESHOLD;

  return {
    lines: displayLines,
    isLoading,
    hasReceivedFirstChunk,
    isStreaming: follow && isLoading,
    // Performance-related state
    lineCount,
    maxRenderLines,
    isNearMaxLines,
    isAtMaxLines,
    isPausedByVisibility,
    // Actions
    clearLogs,
  };
}
