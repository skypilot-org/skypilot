import { act, renderHook } from '@testing-library/react';

import {
  BUILTIN_URL_PATTERNS,
  extractLinksFromLogs,
  useLogLinkExtractor,
} from '@/utils/externalLinks';

jest.mock('@/data/connectors/dashboard_config', () => ({
  getDashboardConfig: jest.fn().mockResolvedValue({ externalLinks: [] }),
}));

const WANDB_URL = 'https://wandb.ai/test-entity/test-project/runs/abc12345';
const WANDB_LINE =
  '(wandb-link-test, pid=886) wandb: 🚀 View run b300-smoke-test at: ' +
  WANDB_URL;

describe('extractLinksFromLogs', () => {
  it('extracts a W&B run URL from a prefixed log line', () => {
    const links = extractLinksFromLogs(
      ['Starting fake training run...', WANDB_LINE],
      BUILTIN_URL_PATTERNS,
      {}
    );
    expect(links).toEqual({ 'W&B Run': WANDB_URL });
  });

  it('ignores non-run W&B URLs (project page)', () => {
    const links = extractLinksFromLogs(
      ['wandb: ⭐️ View project at: https://wandb.ai/test-entity/test-project'],
      BUILTIN_URL_PATTERNS,
      {}
    );
    expect(links).toEqual({});
  });

  it('strips ANSI escape codes around the URL', () => {
    const ansiLine = `\x1b[36mwandb:\x1b[0m View run at: ${WANDB_URL}\x1b[0m`;
    const links = extractLinksFromLogs([ansiLine], BUILTIN_URL_PATTERNS, {});
    expect(links).toEqual({ 'W&B Run': WANDB_URL });
  });

  it('preserves existing matches', () => {
    const existing = { 'W&B Run': 'https://wandb.ai/a/b/runs/first' };
    const links = extractLinksFromLogs(
      [WANDB_LINE],
      BUILTIN_URL_PATTERNS,
      existing
    );
    expect(links).toEqual(existing);
  });
});

describe('useLogLinkExtractor', () => {
  // Flush the async admin-config fetch inside useCustomUrlPatterns so
  // its setState lands inside act().
  const flushConfigFetch = () => act(async () => {});

  it('accumulates links from line arrays (OSS streamer path)', async () => {
    const { result } = renderHook(() => useLogLinkExtractor());
    await flushConfigFetch();
    expect(result.current.extractedLinks).toEqual({});

    act(() => {
      result.current.scanLines(['no links here']);
    });
    expect(result.current.extractedLinks).toEqual({});

    act(() => {
      result.current.scanLines([WANDB_LINE]);
    });
    expect(result.current.extractedLinks).toEqual({ 'W&B Run': WANDB_URL });

    // A later scan without the link (e.g. a streaming buffer reset)
    // must not lose the accumulated match.
    act(() => {
      result.current.scanLines(['later lines without links']);
    });
    expect(result.current.extractedLinks).toEqual({ 'W&B Run': WANDB_URL });
  });

  it('accepts a raw newline-separated buffer (plugin slot path)', async () => {
    const { result } = renderHook(() => useLogLinkExtractor());
    await flushConfigFetch();

    act(() => {
      result.current.scanLines(`step 1: loss 0.5\n${WANDB_LINE}\nstep 2\n`);
    });
    expect(result.current.extractedLinks).toEqual({ 'W&B Run': WANDB_URL });
  });

  it('keeps scanLines referentially stable across renders', async () => {
    const { result, rerender } = renderHook(() => useLogLinkExtractor());
    await flushConfigFetch();
    const first = result.current.scanLines;
    rerender();
    expect(result.current.scanLines).toBe(first);
  });
});
