import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { stripAnsiCodes, shouldDropLogLine } from '@/components/utils';

const TRUNCATION_MARK = ' … [truncated]';

// Ceiling on the raw carry, as a multiple of the visible cap. Only reached
// by output whose visible length barely grows - a writer emitting mostly
// escape sequences - where the visible cap alone would never trip.
const RAW_CARRY_FACTOR = 4;

/**
 * Shared log streaming hook used by both managed job and cluster pages.
 */
export function useLogStreamer({
  streamFn,
  streamArgs,
  enabled = true,
  refreshTrigger = 0,
  maxLineChars = 2000,
  maxRenderLines = 5000,
  flushIntervalMs = 100,
  onError = (error) => {
    // mark parameter as used to satisfy lint while keeping signature
    void error;
  },
}) {
  const [logLines, setLogLines] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [hasReceivedFirstChunk, setHasReceivedFirstChunk] = useState(false);
  const [progressTick, setProgressTick] = useState(0);

  const bufferRef = useRef([]);
  const partialLineRef = useRef('');
  // True while discarding the tail of a line already emitted truncated.
  const overLongLineRef = useRef(false);
  const progressMapRef = useRef(new Map());
  const flushTimerRef = useRef(null);
  const controllerRef = useRef(null);
  const hasFirstChunkRef = useRef(false);
  const onErrorRef = useRef(onError);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  const resetState = useCallback(() => {
    bufferRef.current = [];
    partialLineRef.current = '';
    overLongLineRef.current = false;
    progressMapRef.current = new Map();
    hasFirstChunkRef.current = false;
    setLogLines([]);
    setHasReceivedFirstChunk(false);
  }, []);

  // progressTick triggers recalc when progress updates (progressMapRef is a ref)
  const displayLines = useMemo(
    () => [...logLines, ...Array.from(progressMapRef.current.values())],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [logLines, progressTick]
  );

  const flushBufferedLines = useCallback(() => {
    if (bufferRef.current.length === 0) return;
    setLogLines((prev) => {
      const next = [...prev, ...bufferRef.current];
      bufferRef.current = [];
      return next.length > maxRenderLines
        ? next.slice(next.length - maxRenderLines)
        : next;
    });
  }, [maxRenderLines]);

  useEffect(() => {
    let active = true;
    resetState();

    if (!enabled || !streamFn) {
      setIsLoading(false);
      return () => {};
    }

    const controller = new AbortController();
    controllerRef.current = controller;

    const maxRawCarryChars = maxLineChars * RAW_CARRY_FACTOR;

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

    // Everything a completed line goes through. The last line of a stream
    // has no trailing newline and used to bypass all of it.
    const finalizeLine = (line) => {
      const cleanLine = stripAnsiCodes(line);
      if (shouldDropLogLine(cleanLine)) return null;
      return cleanLine.length > maxLineChars
        ? cleanLine.slice(0, maxLineChars) + TRUNCATION_MARK
        : cleanLine;
    };

    // Where a finished line lands: a progress bar collapses onto its
    // previous update, everything else appends. Shared with the
    // stream-completion path below, so a final progress update replaces its
    // predecessor instead of appearing beside it.
    const emitLine = (cleanLine) => {
      const isProgressBar = /\d+%\s*\|/.test(cleanLine);
      if (isProgressBar) {
        appendProgressLine(cleanLine);
        return;
      }
      bufferRef.current.push(cleanLine);
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

      // maxLineChars only ever applied to lines that had already arrived
      // whole, so output with no newline in it - one very long line, or a
      // writer that only emits carriage returns - grew the carry for the
      // length of the stream. Emit such a line once, truncated, then
      // discard the rest of it until its newline shows up: carrying the
      // remainder instead would both regrow the carry and make the dropped
      // tail reappear as a line of its own.
      if (overLongLineRef.current) {
        if (parts.length > 0) {
          parts.shift();
          overLongLineRef.current = false;
        } else {
          partialLineRef.current = '';
        }
      }
      const newLines = parts.filter((line) => line.trim());

      // Bound the carry on both axes. maxLineChars is a cap on what the
      // reader sees, so measuring the raw string cuts a colour-heavy line
      // short of it; but output that is almost entirely escape sequences
      // keeps a short visible length while the raw string grows without
      // limit, which is the unbounded carry this is here to prevent.
      // Whichever bound trips, the rest of the line is dropped, so what is
      // emitted always says it was truncated.
      let forcedLine = null;
      if (!overLongLineRef.current) {
        const visible = stripAnsiCodes(partialLineRef.current);
        if (
          visible.length > maxLineChars ||
          partialLineRef.current.length > maxRawCarryChars
        ) {
          if (!shouldDropLogLine(visible)) {
            forcedLine = visible.slice(0, maxLineChars) + TRUNCATION_MARK;
          }
          partialLineRef.current = '';
          overLongLineRef.current = true;
        }
      }

      if (
        !hasFirstChunkRef.current &&
        (newLines.length > 0 || forcedLine !== null)
      ) {
        hasFirstChunkRef.current = true;
        setHasReceivedFirstChunk(true);
      }

      for (const line of newLines) {
        const cleanLine = finalizeLine(line);
        if (cleanLine !== null) {
          emitLine(cleanLine);
        }
      }
      // After this chunk's completed lines: the carry sits behind them.
      if (forcedLine !== null) {
        emitLine(forcedLine);
      }
      scheduleFlush();
    };

    setIsLoading(true);

    streamFn({
      ...streamArgs,
      onNewLog: (chunk) => {
        if (active) {
          processChunk(chunk);
        }
      },
      signal: controller.signal,
    })
      .then(() => {
        if (!active) return;
        // The stream's last line has no trailing newline; give it the same
        // treatment as every other line rather than pushing it raw. Nothing
        // to emit if it is only the tail of an already-truncated line.
        if (partialLineRef.current && !overLongLineRef.current) {
          const finalLine = finalizeLine(partialLineRef.current);
          if (finalLine !== null) {
            emitLine(finalLine);
          }
        }
        partialLineRef.current = '';
        overLongLineRef.current = false;
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
      overLongLineRef.current = false;
      progressMapRef.current.clear();
    };
  }, [
    streamFn,
    streamArgs,
    enabled,
    refreshTrigger,
    flushBufferedLines,
    flushIntervalMs,
    maxLineChars,
    maxRenderLines,
    resetState,
  ]);

  return {
    lines: displayLines,
    isLoading,
    hasReceivedFirstChunk,
  };
}
