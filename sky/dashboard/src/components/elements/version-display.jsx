import React, { useState, useEffect } from 'react';
import { NonCapitalizedTooltip } from '@/components/utils';
import { apiClient } from '@/data/connectors/client';

export function VersionDisplay() {
  const [version, setVersion] = useState(null);
  const [latestVersion, setLatestVersion] = useState(null);

  const getVersion = async () => {
    const data = await apiClient.get('/api/health');
    if (!data.ok) {
      console.error(
        `API request /api/health failed with status ${data.status}`
      );
      return;
    }
    const healthData = await data.json();
    if (healthData.version) {
      setVersion(healthData.version);
    }
    if (healthData.latest_version) {
      setLatestVersion(healthData.latest_version);
    }
  };

  useEffect(() => {
    getVersion();
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
