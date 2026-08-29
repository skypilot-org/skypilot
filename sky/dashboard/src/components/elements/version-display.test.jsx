/**
 * Tests for version-display plugin filtering.
 *
 * These assert on `VersionTooltipContent` -- what the tooltip shows once open --
 * rather than on `VersionTooltip`, whose NextUI host renders nothing until it is
 * hovered. Asserting on the closed tooltip is what made every case here fail
 * from the day they were written.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { VersionTooltipContent } from './version-display';

describe('VersionDisplay - Plugin Filtering', () => {
  describe('VersionTooltip filters hidden plugins', () => {
    test('should display visible plugins', () => {
      const plugins = [
        {
          name: 'VisiblePlugin1',
          version: '1.0.0',
          commit: 'abc123',
          hidden_from_display: false,
        },
        {
          name: 'VisiblePlugin2',
          version: '2.0.0',
          commit: 'def456',
          hidden_from_display: false,
        },
      ];

      const { container } = render(
        <VersionTooltipContent
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        />
      );

      // Check that both visible plugins are rendered
      expect(container.textContent).toContain('VisiblePlugin1');
      expect(container.textContent).toContain('VisiblePlugin2');
      expect(container.textContent).toContain('1.0.0');
      expect(container.textContent).toContain('2.0.0');
    });

    test('should exclude hidden plugins from display', () => {
      const plugins = [
        {
          name: 'VisiblePlugin',
          version: '1.0.0',
          commit: 'abc123',
          hidden_from_display: false,
        },
        {
          name: 'HiddenPlugin',
          version: '2.0.0',
          commit: 'def456',
          hidden_from_display: true,
        },
      ];

      const { container } = render(
        <VersionTooltipContent
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        />
      );

      // Check that visible plugin is rendered
      expect(container.textContent).toContain('VisiblePlugin');
      expect(container.textContent).toContain('1.0.0');

      // Check that hidden plugin is NOT rendered
      expect(container.textContent).not.toContain('HiddenPlugin');
      expect(container.textContent).not.toContain('2.0.0');
    });

    test('should handle plugins without hidden_from_display property (defaults to visible)', () => {
      const plugins = [
        {
          name: 'PluginWithoutFlag',
          version: '1.0.0',
          commit: 'abc123',
          // No hidden_from_display property
        },
      ];

      const { container } = render(
        <VersionTooltipContent
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        />
      );

      // Plugin without the flag should still be displayed (defensive filtering)
      expect(container.textContent).toContain('PluginWithoutFlag');
      expect(container.textContent).toContain('1.0.0');
    });

    test('should filter multiple hidden plugins', () => {
      const plugins = [
        {
          name: 'VisiblePlugin',
          version: '1.0.0',
          commit: 'abc123',
          hidden_from_display: false,
        },
        {
          name: 'HiddenPlugin1',
          version: '2.0.0',
          commit: 'def456',
          hidden_from_display: true,
        },
        {
          name: 'HiddenPlugin2',
          version: '3.0.0',
          commit: 'ghi789',
          hidden_from_display: true,
        },
        {
          name: 'AnotherVisiblePlugin',
          version: '4.0.0',
          commit: 'jkl012',
          hidden_from_display: false,
        },
      ];

      const { container } = render(
        <VersionTooltipContent
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        />
      );

      // Check that visible plugins are rendered
      expect(container.textContent).toContain('VisiblePlugin');
      expect(container.textContent).toContain('AnotherVisiblePlugin');

      // Check that hidden plugins are NOT rendered
      expect(container.textContent).not.toContain('HiddenPlugin1');
      expect(container.textContent).not.toContain('HiddenPlugin2');
    });

    test('should handle empty plugins array', () => {
      const plugins = [];

      const { container } = render(
        <VersionTooltipContent
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        />
      );

      // Still shows the commit, and labels it plainly: "Core commit" only
      // earns its qualifier when plugin commits are listed beside it.
      expect(container.textContent).toContain('Commit: core123');
      expect(container.textContent).not.toContain('Core commit');
    });

    test('should handle all plugins being hidden', () => {
      const plugins = [
        {
          name: 'HiddenPlugin1',
          version: '1.0.0',
          commit: 'abc123',
          hidden_from_display: true,
        },
        {
          name: 'HiddenPlugin2',
          version: '2.0.0',
          commit: 'def456',
          hidden_from_display: true,
        },
      ];

      const { container } = render(
        <VersionTooltipContent
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        />
      );

      // Should not show any plugin names
      expect(container.textContent).not.toContain('HiddenPlugin1');
      expect(container.textContent).not.toContain('HiddenPlugin2');

      // Still shows the commit, and labels it the same way as the no-plugins
      // case: with nothing listed beside it, there is nothing for "Core" to
      // distinguish it from.
      expect(container.textContent).toContain('Commit: core123');
      expect(container.textContent).not.toContain('Core commit');
    });
  });
});
