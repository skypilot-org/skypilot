import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { stripAnsiCodes, shouldDropLogLine } from '@/components/utils';

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
