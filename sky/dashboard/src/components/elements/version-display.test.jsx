/**
 * Tests for version-display component plugin filtering
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { VersionTooltip } from './version-display';

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
        <VersionTooltip
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        >
          <div>Version</div>
        </VersionTooltip>
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
        <VersionTooltip
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        >
          <div>Version</div>
        </VersionTooltip>
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
        <VersionTooltip
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        >
          <div>Version</div>
        </VersionTooltip>
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
        <VersionTooltip
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        >
          <div>Version</div>
        </VersionTooltip>
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
        <VersionTooltip
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        >
          <div>Version</div>
        </VersionTooltip>
      );

      // Should still show commit info
      expect(container.textContent).toContain('Core commit');
      expect(container.textContent).toContain('core123');
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
        <VersionTooltip
          version="1.0.0"
          commit="core123"
          plugins={plugins}
          showCommit={true}
        >
          <div>Version</div>
        </VersionTooltip>
      );

      // Should not show any plugin names
      expect(container.textContent).not.toContain('HiddenPlugin1');
      expect(container.textContent).not.toContain('HiddenPlugin2');

      // Should still show commit info
      expect(container.textContent).toContain('Core commit');
      expect(container.textContent).toContain('core123');
    });
  });
});
