import { act, renderHook } from '@testing-library/react';

import {
  BUILTIN_URL_PATTERNS,
  extractLinksFromLogs,
  resolveTemplateLinks,
  useLogLinkExtractor,
  useTemplateLinks,
} from '@/utils/externalLinks';
import { getDashboardConfig } from '@/data/connectors/dashboard_config';

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

  it('skips non-string entries without throwing', () => {
    const links = extractLinksFromLogs(
      [null, undefined, 42, { msg: 'metadata' }, WANDB_LINE],
      BUILTIN_URL_PATTERNS,
      {}
    );
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

describe('resolveTemplateLinks', () => {
  const RAY_ENTRY = {
    label: 'Ray Dashboard',
    url: 'https://ray.internal.example.com/dashboard/${cluster_name}',
  };

  it('substitutes context values into ${var} placeholders', () => {
    const links = resolveTemplateLinks([RAY_ENTRY], {
      cluster_name: 'my-cluster',
    });
    expect(links).toEqual({
      'Ray Dashboard': 'https://ray.internal.example.com/dashboard/my-cluster',
    });
  });

  it('URI-encodes substituted values', () => {
    const links = resolveTemplateLinks(
      [{ label: 'Jobs', url: 'https://exp.internal/jobs?name=${job_name}' }],
      { job_name: 'train run/v2' }
    );
    expect(links).toEqual({
      Jobs: 'https://exp.internal/jobs?name=train%20run%2Fv2',
    });
  });

  it('skips entries whose variables are missing or empty in the context', () => {
    const entries = [
      RAY_ENTRY,
      { label: 'Job page', url: 'https://exp.internal/jobs/${job_id}' },
    ];
    // Cluster page context: no job_id, so only the Ray link resolves.
    const links = resolveTemplateLinks(entries, {
      cluster_name: 'my-cluster',
      job_id: undefined,
    });
    expect(links).toEqual({
      'Ray Dashboard': 'https://ray.internal.example.com/dashboard/my-cluster',
    });
  });

  it('passes through static urls with no variables', () => {
    const links = resolveTemplateLinks(
      [{ label: 'Wiki', url: 'https://wiki.internal/skypilot' }],
      {}
    );
    expect(links).toEqual({ Wiki: 'https://wiki.internal/skypilot' });
  });

  it('ignores regex entries and malformed input', () => {
    const links = resolveTemplateLinks(
      [
        { label: 'Grafana', regex: 'https://grafana\\.internal/.*' },
        null,
        { label: 42, url: 'https://example.com' },
      ],
      { cluster_name: 'my-cluster' }
    );
    expect(links).toEqual({});
    expect(resolveTemplateLinks(null, {})).toEqual({});
  });

  it('substitutes numeric context values (job ids)', () => {
    const links = resolveTemplateLinks(
      [{ label: 'Job page', url: 'https://exp.internal/jobs/${job_id}' }],
      { job_id: 7 }
    );
    expect(links).toEqual({ 'Job page': 'https://exp.internal/jobs/7' });
  });

  it('skips non-allowlisted variables, including Object.prototype names', () => {
    // ${toString} must not resolve to Object.prototype.toString; the entry
    // is skipped like any other unresolvable variable. Server-side config
    // validation rejects these, but the client must not trust the config.
    const links = resolveTemplateLinks(
      [
        { label: 'Proto', url: 'https://x.internal/${toString}' },
        { label: 'Ctor', url: 'https://x.internal/${constructor}' },
        { label: 'Ok', url: 'https://x.internal/${cluster_name}' },
      ],
      { cluster_name: 'my-cluster' }
    );
    expect(links).toEqual({ Ok: 'https://x.internal/my-cluster' });
  });
});

describe('useTemplateLinks', () => {
  const flushConfigFetch = () => act(async () => {});

  it('resolves admin-configured url templates against the context', async () => {
    getDashboardConfig.mockResolvedValueOnce({
      externalLinks: [
        {
          label: 'Ray Dashboard',
          url: 'https://ray.internal.example.com/dashboard/${cluster_name}',
        },
        { label: 'Grafana', regex: 'https://grafana\\.internal/.*' },
      ],
    });
    const { result } = renderHook(() =>
      useTemplateLinks({ cluster_name: 'my-cluster' })
    );
    await flushConfigFetch();
    expect(result.current).toEqual({
      'Ray Dashboard': 'https://ray.internal.example.com/dashboard/my-cluster',
    });
  });
});
