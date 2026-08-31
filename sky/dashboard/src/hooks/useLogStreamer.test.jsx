import { renderHook, waitFor } from '@testing-library/react';
import { useLogStreamer } from './useLogStreamer';

// `streamFn` and `streamArgs` are effect dependencies, so they have to be
// stable across renders or the hook restarts its stream forever.
const STREAM_ARGS = {};

function streamOf(chunks, { ends = true } = {}) {
  return async ({ onNewLog }) => {
    for (const chunk of chunks) {
      onNewLog(chunk);
    }
    // A stream that never closes is the only way to see what the hook has
    // rendered *while* output is still arriving, as opposed to what it
    // flushes on the way out.
    if (!ends) await new Promise(() => {});
  };
}

async function linesFrom(chunks, options = {}) {
  const streamFn = streamOf(chunks);
  const { result } = renderHook(() =>
    useLogStreamer({
      streamFn,
      streamArgs: STREAM_ARGS,
      flushIntervalMs: 0,
      ...options,
    })
  );
  await waitFor(() => expect(result.current.isLoading).toBe(false));
  await waitFor(() => expect(result.current.lines.length).toBeGreaterThan(0));
  return result.current.lines;
}

describe('useLogStreamer line assembly', () => {
  it('joins a line split across chunks', async () => {
    expect(await linesFrom(['par', 'tial line\n'])).toEqual(['partial line']);
  });

  it('emits the last line when the stream ends without a newline', async () => {
    expect(await linesFrom(['one\n', 'two'])).toEqual(['one', 'two']);
  });

  // The last line used to be pushed raw: no ANSI stripping, no drop filter
  // and no length cap, unlike every line before it.
  it('strips ANSI from the last line, like any other line', async () => {
    expect(await linesFrom(['\x1b[32mgreen\x1b[0m\n', '\x1b[31mred'])).toEqual([
      'green',
      'red',
    ]);
  });

  it('drops a control line that arrives last, like any other line', async () => {
    const lines = await linesFrom([
      'real output\n',
      '<sky-payload>{"returncode": 0}</sky-payload>',
    ]);
    expect(lines).toEqual(['real output']);
  });

  it('bounds a carry built from many newline-free chunks', async () => {
    const chunks = Array.from({ length: 20 }, () => 'y'.repeat(10));
    const lines = await linesFrom(chunks, { maxLineChars: 30 });
    // One truncated line, not twenty, and not one 200-character line.
    expect(lines).toEqual(['y'.repeat(30) + ' … [truncated]']);
  });

  // The bound has to act during the stream, not on the way out: an
  // unbounded carry produces the same final render, and differs only in
  // how much of it the browser was holding while the job ran.
  it('surfaces an over-long line while the stream is still open', async () => {
    const streamFn = streamOf(
      Array.from({ length: 20 }, () => 'y'.repeat(10)),
      { ends: false }
    );
    const { result } = renderHook(() =>
      useLogStreamer({
        streamFn,
        streamArgs: STREAM_ARGS,
        flushIntervalMs: 0,
        maxLineChars: 30,
      })
    );
    await waitFor(() => expect(result.current.lines).toHaveLength(1));
    expect(result.current.lines[0]).toBe('y'.repeat(30) + ' … [truncated]');
    // Still streaming: this is not the end-of-stream flush.
    expect(result.current.isLoading).toBe(true);
  });

  it('discards the rest of an over-long line, then resumes', async () => {
    const lines = await linesFrom(
      ['z'.repeat(60), 'still the same line\n', 'next line\n'],
      { maxLineChars: 10 }
    );
    expect(lines).toEqual(['z'.repeat(10) + ' … [truncated]', 'next line']);
  });

  it('keeps ordinary lines intact around an over-long one', async () => {
    const lines = await linesFrom(['a\n', 'b'.repeat(40) + '\n', 'c\n'], {
      maxLineChars: 10,
    });
    expect(lines).toEqual(['a', 'b'.repeat(10) + ' … [truncated]', 'c']);
  });
});
