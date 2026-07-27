import { act, renderHook } from '@testing-library/react';

import {
  BUILTIN_URL_PATTERNS,
  compileCustomPatterns,
  extractLinksFromLogs,
  filterLinksByScope,
  isEntryInScope,
  LINK_SCOPE_CLUSTER,
  LINK_SCOPE_JOBS,
  resolveTemplateLinks,
  useLogLinkExtractor,
  useScopedLinks,
  useTemplateLinks,
} from '@/utils/externalLinks';
import { getDashboardConfig } from '@/data/connectors/dashboard_config';

jest.mock('@/data/connectors/dashboard_config', () => ({
  // The scope constants are real values, not test doubles; only the
  // network-touching fetch is mocked.
  ...jest.requireActual('@/data/connectors/dashboard_config'),
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

  it('excludes entries scoped to other pages', async () => {
    getDashboardConfig.mockResolvedValueOnce({
      externalLinks: [
        {
          label: 'Ray Dashboard',
          url: 'https://ray.internal.example.com/dashboard/${cluster_name}',
          scope: [LINK_SCOPE_CLUSTER],
        },
        {
          label: 'Wiki',
          url: 'https://wiki.internal/skypilot',
        },
      ],
    });
    const { result } = renderHook(() =>
      useTemplateLinks({ cluster_name: 'my-cluster' }, LINK_SCOPE_JOBS)
    );
    await flushConfigFetch();
    expect(result.current).toEqual({
      Wiki: 'https://wiki.internal/skypilot',
    });
  });
});

describe('isEntryInScope', () => {
  it('treats entries without a scope as visible everywhere', () => {
    expect(isEntryInScope({ label: 'X' }, LINK_SCOPE_CLUSTER)).toBe(true);
    expect(isEntryInScope({ label: 'X', scope: [] }, LINK_SCOPE_JOBS)).toBe(
      true
    );
    expect(isEntryInScope(null, LINK_SCOPE_CLUSTER)).toBe(true);
  });

  it('disables filtering when no page scope is given', () => {
    expect(isEntryInScope({ scope: [LINK_SCOPE_CLUSTER] }, undefined)).toBe(
      true
    );
  });

  it('matches the page scope against the scope list', () => {
    const entry = { scope: [LINK_SCOPE_CLUSTER] };
    expect(isEntryInScope(entry, LINK_SCOPE_CLUSTER)).toBe(true);
    expect(isEntryInScope(entry, LINK_SCOPE_JOBS)).toBe(false);
    const both = { scope: [LINK_SCOPE_CLUSTER, LINK_SCOPE_JOBS] };
    expect(isEntryInScope(both, LINK_SCOPE_JOBS)).toBe(true);
  });
});

describe('scope filtering', () => {
  const CLUSTER_ONLY_URL = {
    label: 'Ray Dashboard',
    url: 'https://ray.internal.example.com/dashboard/${cluster_name}',
    scope: [LINK_SCOPE_CLUSTER],
  };
  const JOBS_ONLY_REGEX = {
    label: 'Experiment',
    regex: 'https://exp\\.internal/.*',
    scope: [LINK_SCOPE_JOBS],
  };
  const UNSCOPED_REGEX = {
    label: 'Grafana',
    regex: 'https://grafana\\.internal/.*',
  };

  it('compileCustomPatterns skips entries scoped to other pages', () => {
    const entries = [JOBS_ONLY_REGEX, UNSCOPED_REGEX];
    expect(
      Object.keys(compileCustomPatterns(entries, LINK_SCOPE_CLUSTER))
    ).toEqual(['Grafana']);
    expect(
      Object.keys(compileCustomPatterns(entries, LINK_SCOPE_JOBS))
    ).toEqual(['Experiment', 'Grafana']);
    // No page scope: no filtering (back-compat).
    expect(Object.keys(compileCustomPatterns(entries))).toEqual([
      'Experiment',
      'Grafana',
    ]);
  });

  it('resolveTemplateLinks skips entries scoped to other pages even when all variables resolve', () => {
    const context = { cluster_name: 'my-cluster' };
    expect(
      resolveTemplateLinks([CLUSTER_ONLY_URL], context, LINK_SCOPE_JOBS)
    ).toEqual({});
    expect(
      resolveTemplateLinks([CLUSTER_ONLY_URL], context, LINK_SCOPE_CLUSTER)
    ).toEqual({
      'Ray Dashboard': 'https://ray.internal.example.com/dashboard/my-cluster',
    });
    // No page scope: no filtering (back-compat).
    expect(resolveTemplateLinks([CLUSTER_ONLY_URL], context)).toEqual({
      'Ray Dashboard': 'https://ray.internal.example.com/dashboard/my-cluster',
    });
  });

  describe('filterLinksByScope', () => {
    const DB_LINKS = {
      Experiment: 'https://exp.internal/run/1',
      Grafana: 'https://grafana.internal/d/abc',
      'AWS Console': 'https://console.aws.amazon.com/ec2',
    };

    it('removes links whose configured entries are all out of scope', () => {
      const filtered = filterLinksByScope(
        DB_LINKS,
        [JOBS_ONLY_REGEX, UNSCOPED_REGEX],
        LINK_SCOPE_CLUSTER
      );
      // 'Experiment' is jobs-only; unscoped and unconfigured labels stay.
      expect(filtered).toEqual({
        Grafana: 'https://grafana.internal/d/abc',
        'AWS Console': 'https://console.aws.amazon.com/ec2',
      });
    });

    it('keeps a label when any duplicate entry is in scope', () => {
      const filtered = filterLinksByScope(
        { Experiment: 'https://exp.internal/run/1' },
        [
          JOBS_ONLY_REGEX,
          { label: 'Experiment', regex: 'x', scope: [LINK_SCOPE_CLUSTER] },
        ],
        LINK_SCOPE_CLUSTER
      );
      expect(filtered).toEqual({ Experiment: 'https://exp.internal/run/1' });
    });

    it('is a no-op without a page scope or entries', () => {
      expect(filterLinksByScope(DB_LINKS, [JOBS_ONLY_REGEX], undefined)).toBe(
        DB_LINKS
      );
      expect(filterLinksByScope(DB_LINKS, null, LINK_SCOPE_CLUSTER)).toBe(
        DB_LINKS
      );
      expect(
        filterLinksByScope(null, [JOBS_ONLY_REGEX], LINK_SCOPE_JOBS)
      ).toEqual({});
    });

    it('preserves referential identity when nothing is removed', () => {
      const filtered = filterLinksByScope(
        DB_LINKS,
        [UNSCOPED_REGEX],
        LINK_SCOPE_CLUSTER
      );
      expect(filtered).toBe(DB_LINKS);
    });
  });

  it('useScopedLinks filters server-computed links by scope', async () => {
    getDashboardConfig.mockResolvedValueOnce({
      externalLinks: [CLUSTER_ONLY_URL, UNSCOPED_REGEX],
    });
    const links = {
      'Ray Dashboard': 'https://ray.internal.example.com/dashboard/c1',
      Grafana: 'https://grafana.internal/d/abc',
    };
    const { result } = renderHook(() => useScopedLinks(links, LINK_SCOPE_JOBS));
    await act(async () => {});
    expect(result.current).toEqual({
      Grafana: 'https://grafana.internal/d/abc',
    });
  });
});
