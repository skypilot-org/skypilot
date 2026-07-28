/**
 * The read-only hint on a non-writable cluster's Connect/VSCode actions is a
 * full sentence, so it must render as written.
 *
 * `CustomTooltip` hardcodes `capitalize` on the span that renders the tooltip
 * content, and a `className` passed from the call site lands on the wrapper
 * instead — so it cannot override that. CSS `capitalize` upper-cases EVERY
 * word, which turned the hint into "Read-only: You Are Not A Member Of This
 * Cluster's Workspace". The sentence therefore has to go through
 * `NonCapitalizedTooltip` (whose content span is `normal-case`), while the
 * short action labels ("Connect with SSH") keep the capitalizing tooltip.
 *
 * NextUI mounts tooltip content lazily, so asserting on the rendered content
 * span would require driving a hover. What actually decides the casing is
 * *which tooltip component* wraps the action, so that is what these tests pin.
 */
import '@testing-library/jest-dom';

import { render, screen } from '@testing-library/react';
import React from 'react';

jest.mock('@/hooks/useMobile', () => ({ useMobile: () => false }));
jest.mock('@/lib/analytics', () => ({
  trackClusterAction: jest.fn(),
  trackFilterUsed: jest.fn(),
}));

// Replace only the two tooltip components, so the content each one receives
// becomes inspectable. Everything else in the module is kept.
jest.mock('@/components/utils', () => {
  const actual = jest.requireActual('@/components/utils');
  const stub = (testId) => {
    const Stub = ({ content, children }) => (
      <div data-testid={testId} data-content={String(content ?? '')}>
        {children}
      </div>
    );
    Stub.displayName = testId;
    return Stub;
  };
  return {
    ...actual,
    CustomTooltip: stub('capitalizing-tooltip'),
    NonCapitalizedTooltip: stub('non-capitalizing-tooltip'),
  };
});

const { Status2Actions } = require('@/components/clusters');

const READ_ONLY_HINT =
  'Read-only: you are not a member of this cluster’s workspace';

describe('read-only action hint', () => {
  it('goes through the non-capitalizing tooltip', () => {
    render(
      <Status2Actions
        cluster="ro-cluster"
        status="UP"
        withLabel={true}
        writable={false}
      />
    );
    const hints = screen
      .getAllByTestId('non-capitalizing-tooltip')
      .map((e) => e.getAttribute('data-content'));
    expect(hints).toContain(READ_ONLY_HINT);
    // And never through the capitalizing one, which would title-case it.
    const capitalized = screen
      .queryAllByTestId('capitalizing-tooltip')
      .map((e) => e.getAttribute('data-content'));
    expect(capitalized).not.toContain(READ_ONLY_HINT);
  });

  it('leaves the short action labels on the capitalizing tooltip', () => {
    render(
      <Status2Actions cluster="rw-cluster" status="UP" withLabel={true} />
    );
    const labels = screen
      .getAllByTestId('capitalizing-tooltip')
      .map((e) => e.getAttribute('data-content'));
    expect(labels).toContain('Connect with SSH');
    expect(screen.queryAllByTestId('non-capitalizing-tooltip')).toHaveLength(0);
  });
});
