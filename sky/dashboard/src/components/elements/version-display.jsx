import React, { useState, useEffect } from 'react';
import { NonCapitalizedTooltip } from '@/components/utils';
import { apiClient } from '@/data/connectors/client';

export function VersionDisplay() {
  const [version, setVersion] = useState(null);
  const [latestVersion, setLatestVersion] = useState(null);
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
      if (healthData.latest_version) {
        setLatestVersion(healthData.latest_version);
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

  // Only show light bulb icon if there's an upgrade available
  if (!latestVersion) return null;

  // Create tooltip content with proper line breaks
  const tooltipContent = (
    <div>
      <div className="font-semibold">Update Available</div>
      <br />
      <div>Current version: {version}</div>
      <div>New version available: {latestVersion}</div>
    </div>
  );

  return (
    <NonCapitalizedTooltip
      content={tooltipContent}
      className="text-sm text-muted-foreground"
    >
      <div className="inline-flex items-center cursor-help">
        <span className="text-yellow-600 text-lg" title="Update Available">
          💡
        </span>
      </div>
    </NonCapitalizedTooltip>
  );
}
