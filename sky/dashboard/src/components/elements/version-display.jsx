import React, { useState, useEffect } from 'react';
import { NonCapitalizedTooltip } from '@/components/utils';
import { apiClient } from '@/data/connectors/client';

export function VersionDisplay() {
  const [version, setVersion] = useState(null);
  const [commit, setCommit] = useState(null);
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
    if (healthData.commit) {
      setCommit(healthData.commit);
    }
    if (healthData.latest_version) {
      setLatestVersion(healthData.latest_version);
    }
  };

  useEffect(() => {
    getVersion();
  }, []);

  if (!version) return null;

  // Deduce if it's a nightly version and generate upgrade command
  const isNightly = latestVersion && latestVersion.toLowerCase().includes('.dev');
  const upgradeCommand = latestVersion
    ? (isNightly
        ? 'pip install --upgrade --pre skypilot-nightly'
        : 'pip install --upgrade skypilot')
    : null;

  // Create tooltip text
  let tooltipText = commit
    ? `Commit: ${commit}`
    : 'Commit information not available';

  // Add upgrade hint to tooltip if available
  if (latestVersion && upgradeCommand) {
    tooltipText += `\n\n💡 New version available: ${latestVersion}\nUpgrade: ${upgradeCommand}`;
  }

  return (
    <NonCapitalizedTooltip
      content={tooltipText}
      className="text-sm text-muted-foreground"
    >
      <div className="text-sm text-gray-500 cursor-help border-b border-dotted border-gray-400 inline-block">
        Version: {version}
        {latestVersion && upgradeCommand && (
          <span className="ml-2 text-yellow-600">💡 Update available</span>
        )}
      </div>
    </NonCapitalizedTooltip>
  );
}
