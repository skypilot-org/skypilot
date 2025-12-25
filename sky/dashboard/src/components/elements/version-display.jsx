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

  // Create tooltip text
  const tooltipLines = [];
  if (commit) {
    if (plugins.length > 0) {
      tooltipLines.push(`Core commit: ${commit}`);
    } else {
      tooltipLines.push(`Commit: ${commit}`);
    }
  }

  plugins.forEach((plugin) => {
    const pluginName = plugin.name || 'Unknown Plugin';
    const parts = [];
    if (plugin.version) {
      parts.push(`${plugin.version}`);
    }
    if (plugin.commit) {
      parts.push(plugin.commit);
    }
    if (parts.length > 0) {
      tooltipLines.push(`${pluginName}: ${parts.join(' - ')}`);
    }
  });

  const tooltipText =
    tooltipLines.length > 0
      ? tooltipLines.join('\n')
      : 'Commit information not available';

  return (
    <NonCapitalizedTooltip
      content={tooltipText}
      className="text-sm text-muted-foreground"
    >
      <div className="text-sm text-gray-500 cursor-help border-b border-dotted border-gray-400 inline-block">
        Version: {version}
      </div>
    </NonCapitalizedTooltip>
  );
}
