import React, { useState, useEffect } from 'react';
import { NonCapitalizedTooltip } from '@/components/utils';
import { apiClient } from '@/data/connectors/client';

export function VersionDisplay() {
  const [version, setVersion] = useState(null);
  const [commit, setCommit] = useState(null);
  const [plugins, setPlugins] = useState([]);

  const getVersionAndPlugins = async () => {
    // Concurrently fetch health and plugins data
    const [healthResponse, pluginsResponse] = await Promise.all([
      apiClient.get('/api/health'),
      apiClient.get('/api/plugins'),
    ]);

    // Process health data
    if (healthResponse.ok) {
      const healthData = await healthResponse.json();
      if (healthData.version) {
        setVersion(healthData.version);
      }
      if (healthData.commit) {
        setCommit(healthData.commit);
      }
    } else {
      console.error(
        `API request /api/health failed with status ${healthResponse.status}`
      );
    }

    // Process plugins data
    if (pluginsResponse.ok) {
      const pluginsData = await pluginsResponse.json();
      if (pluginsData.plugins && pluginsData.plugins.length > 0) {
        setPlugins(pluginsData.plugins);
      }
    } else {
      console.error(
        `API request /api/plugins failed with status ${pluginsResponse.status}`
      );
    }
  };

  useEffect(() => {
    getVersionAndPlugins();
  }, []);

  if (!version) return null;

  // Create tooltip text (for commit information on hover)
  let tooltipText = '';
  if (plugins.length > 0) {
    // Show plugin commit information
    const pluginCommits = plugins
      .filter((plugin) => plugin.commit)
      .map((plugin) => {
        const pluginName = plugin.name || 'Unknown Plugin';
        return `${pluginName} commit: ${plugin.commit}`;
      });

    if (pluginCommits.length > 0) {
      tooltipText = pluginCommits.join('\n');
      // Add main commit if available
      if (commit) {
        tooltipText = `Core commit: ${commit}\n${tooltipText}`;
      }
    }
  }
  if (tooltipText === '') {
    // No plugins, show only main commit
    if (commit) {
      tooltipText = `Commit: ${commit}`;
    } else {
      tooltipText = 'Commit information not available';
    }
  }

  // Create display text (default: show version)
  const versionItems = [];

  // Add plugin versions
  if (plugins.length > 0) {
    // Add core version
    versionItems.push(`Core version: ${version}`);
    const pluginVersions = plugins
      .filter((plugin) => plugin.version)
      .map((plugin) => {
        const pluginName = plugin.name || 'Unknown Plugin';
        return `${pluginName} version: ${plugin.version}`;
      });
    versionItems.push(...pluginVersions);
  } else {
    // Add core version
    versionItems.push(`Version: ${version}`);
  }

  const displayText = versionItems.join('\n');

  return (
    <NonCapitalizedTooltip
      content={tooltipText}
      className="text-sm text-muted-foreground"
    >
      <div className="text-sm text-gray-500 cursor-help border-b border-dotted border-gray-400 inline-block whitespace-pre-line">
        {displayText}
      </div>
    </NonCapitalizedTooltip>
  );
}
